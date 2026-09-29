/**
 * dsh-local-bridge — hand a locally-running tool the current Web session URL.
 *
 * WHY THIS EXISTS
 *   DSH prints a startup URL carrying a one-shot token, and every later
 *   `/api` call authenticates with the HttpOnly cookie that URL exchanges
 *   for. An out-of-process CLI therefore needs that URL. Today the only way
 *   to get it is for a human to copy it off the terminal, which means the
 *   token ends up in shell history, CI logs, and chat transcripts.
 *
 *   This plugin runs INSIDE the DSH process, where `connection.authenticatedUrl()`
 *   can mint a fresh, valid URL on demand. It exposes that over a loopback
 *   route so local tooling can self-authenticate — no human in the loop, and
 *   nothing to copy around.
 *
 * WHY THE ROUTE IS NOT UNDER /api
 *   DSH authenticates the entire `/api` channel BEFORE dispatching to any
 *   route on it:
 *
 *     handler: async (req, res) => {
 *       const rejection = connection.requestRejection(req);
 *       if (rejection !== void 0) { res.writeHead(rejection); ...; return; }
 *       await bridge(req, res, fetchHandler, ...);
 *     }
 *
 *   A bridge registered at `/api/...` is therefore unreachable by exactly the
 *   unauthenticated caller that needs it — a chicken-and-egg problem. The first
 *   draft of this plugin made that mistake and returned 401 from its own route.
 *   Registering on `webserver` directly puts the route outside the
 *   authenticated channel, which is the entire point of the plugin.
 *
 * SECURITY MODEL — read this before changing anything
 *   The route hands out a credential, so it owns its own protection:
 *     1. Loopback only. The `Host` header must be a loopback literal, so a
 *        request arriving through a tunnel or a DNS rebinding is refused
 *        before any token is minted.
 *     2. Per-boot shared secret. A random 32-byte secret is minted at plugin
 *        start and written 0600 to $DSH_HOME/local-bridge.secret. The caller
 *        must echo it, which defeats any local process that did not start
 *        alongside DSH.
 *     3. Never logged or persisted. The secret file holds only the shared
 *        secret; the authenticated URL exists solely in the response body.
 *
 *   What this does NOT defend against: another process running as the same
 *   user that can read the secret file. Such a process could already attach
 *   to the DSH process. The threat model is "stray local tooling and
 *   accidental disclosure", not "malware already running as me".
 */
import { randomBytes } from "node:crypto";
import { writeFileSync, unlinkSync } from "node:fs";
import { join } from "node:path";
import { homedir } from "node:os";

export const name = "dsh-local-bridge";
export const inject = ["connection", "webServer"];

/**
 * Path holding the per-boot shared secret. 0600, rewritten every boot.
 *
 * Resolved on each call rather than once at module load: a module-level const
 * would freeze whatever `DSH_HOME` happened to be when the file was first
 * imported, so a later re-activation under a different home would write the
 * secret somewhere the client is not looking.
 */
function secretPath() {
	return join(
		process.env.DSH_HOME || join(homedir(), ".dsh"),
		"local-bridge.secret",
	);
}

/** Absolute route path. Deliberately outside `/api` — see the header comment. */
export const BRIDGE_PATH = "/local-bridge/auth";

/** Host headers accepted as loopback. */
const LOOPBACK = new Set(["localhost", "127.0.0.1", "[::1]", "::1"]);

let secret = "";

function mintSecret() {
	return randomBytes(32).toString("base64url");
}

/** Write the shared secret with owner-only permissions. */
function persistSecret(value) {
	const target = secretPath();
	// Passing `mode` only applies on creation, so remove any stale file first
	// to guarantee the secret is never briefly world-readable.
	try {
		unlinkSync(target);
	} catch {}
	writeFileSync(target, value, { mode: 0o600 });
}

/**
 * Is this request actually from loopback?
 *
 * Because the route sits outside DSH's authenticated channel, it owns this
 * check rather than inheriting one.
 */
function isLoopback(req) {
	const raw = String(req.headers?.host ?? "");
	const bare = raw.startsWith("[")
		? raw.slice(0, raw.indexOf("]") + 1)
		: raw.split(":")[0];
	return LOOPBACK.has(bare);
}

function send(res, status, body) {
	res.writeHead(status, {
		"content-type": "application/json",
		"cache-control": "no-store",
	});
	res.end(JSON.stringify(body));
}

export function apply(ctx) {
	secret = mintSecret();
	persistSecret(secret);
	// `ctx.effect` takes a body that RETURNS a disposer, not the cleanup
	// itself. Passing `() => unlinkSync(...)` would unlink during startup and
	// register nothing, so the secret file would outlive the process.
	ctx.effect(() => () => unlinkSync(secretPath()), "dsh-local-bridge: cleanup");

	// The route must be owned by an effect, not registered bare.
	//
	// `register()` returns a disposer that removes the route from the shared
	// table, and it THROWS on a duplicate path. Dropping the disposer means
	// the stale route survives an unload, so the next `apply()` — on any
	// HMR reload or config change — dies with "duplicate exact route".
	// cordis swallows that into a dead fiber, leaving no route and no secret
	// file: a silent, permanent bridge outage. Every other DSH plugin wraps
	// its registration in `ctx.effect` for exactly this reason.
	ctx.effect(() => ctx.webServer.register({
		kind: "exact",
		path: BRIDGE_PATH,
		handler: async (req, res) => {
			if (!isLoopback(req)) {
				send(res, 403, { ok: false, error: "loopback-only" });
				return;
			}

			const presented =
				req.headers["x-dsh-bridge-secret"] ??
				new URL(req.url ?? "/", "http://127.0.0.1").searchParams.get("secret") ??
				"";
			if (!presented || presented !== secret) {
				send(res, 403, { ok: false, error: "bad-secret" });
				return;
			}

			const base = `http://${req.headers.host ?? "127.0.0.1:3080"}`;
			let url;
			try {
				url = ctx.connection.authenticatedUrl(base);
			} catch (error) {
				send(res, 500, {
					ok: false,
					error: String(error?.message ?? error),
				});
				return;
			}

			send(res, 200, { ok: true, url, origin: base, secretPath: secretPath() });
		},
	}), "dsh-local-bridge: route");
}
