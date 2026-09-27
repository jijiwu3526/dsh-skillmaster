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
 * SECURITY MODEL — read this before changing anything
 *   The route hands out a credential. It is guarded by:
 *     1. Loopback origin only. The request's `Host` header must be a
 *        loopback literal; a request arriving through a tunnel or a
 *        rebinding attack is rejected before the token is read.
 *     2. Per-boot shared secret. A random token is minted at plugin start
 *        and written to a 0600 file. The caller must echo it. This defeats
 *        any local process that did not start alongside DSH.
 *     3. Never logs or persists the URL. The secret file holds the shared
 *        secret only, not the authenticated URL.
 *
 *   What this does NOT defend against: another process running as the same
 *   user that can read the secret file. That process could already attach
 *   to the DSH process. The threat model is "stray local tooling and
 *   accidental disclosure", not "malware already running as me".
 */
import { randomBytes, readFileSync, writeFileSync, unlinkSync } from "node:fs";
import { join } from "node:path";
import { homedir } from "node:os";

export const name = "dsh-local-bridge";
export const inject = ["connection"];

/** Path holding the per-boot shared secret. 0600, rewritten every boot. */
const SECRET_PATH = join(
	process.env.DSH_HOME || join(homedir(), ".dsh"),
	"local-bridge.secret",
);

/** Route registered on the shared /api channel. */
export const BRIDGE_PATH = "/api/local-bridge/auth";

/** Loopback literals we accept in a `Host` header. */
const LOOPBACK = new Set(["localhost", "127.0.0.1", "[::1]", "::1"]);

let secret = "";

function mintSecret() {
	return randomBytes(32).toString("base64url");
}

/** Write the shared secret with owner-only permissions. */
function persistSecret(value) {
	// Pre-create with 0600 so the secret is never briefly world-readable.
	writeFileSync(SECRET_PATH, value, { mode: 0o600 });
}

/**
 * Is this request actually from loopback?
 *
 * DSH already applies its own Host/Origin policy before a route sees the
 * request. This repeats the narrow check for the loopback literal so a
 * request forwarded by a tunnel cannot obtain the token even if DSH's
 * broader policy is ever loosened.
 */
function isLoopback(request) {
	const host = request.headers.get("host") ?? "";
	// Strip the port; keep bracketed IPv6 intact.
	const bare = host.startsWith("[")
		? host.slice(0, host.indexOf("]") + 1)
		: host.split(":")[0];
	return LOOPBACK.has(bare);
}

function json(body, status = 200) {
	return new Response(JSON.stringify(body), {
		status,
		headers: { "content-type": "application/json" },
	});
}

function handle(ctx, request) {
	if (!isLoopback(request)) {
		return json({ ok: false, error: "loopback-only" }, 403);
	}

	const presented =
		request.headers.get("x-dsh-bridge-secret") ??
		new URL(request.url).searchParams.get("secret") ??
		"";
	if (!presented || presented !== secret) {
		return json({ ok: false, error: "bad-secret" }, 403);
	}

	// The one thing this plugin exists for: mint a fresh authenticated URL
	// from inside the process, so the caller never has to be handed one.
	const base = `http://${request.headers.get("host") ?? "127.0.0.1:3080"}`;
	let url;
	try {
		url = ctx.connection.authenticatedUrl(base);
	} catch (error) {
		return json({ ok: false, error: String(error?.message ?? error) }, 500);
	}

	// Health facts a caller can act on, without any extra round-trips.
	return json({
		ok: true,
		url,
		origin: base,
		dshHome: process.env.DSH_HOME ?? null,
		secretPath: SECRET_PATH,
	});
}

export function apply(ctx) {
	secret = mintSecret();
	persistSecret(secret);
	ctx.effect(() => unlinkSync(SECRET_PATH), "dsh-local-bridge: cleanup");

	ctx.connection.fetch.register({
		path: BRIDGE_PATH,
		methods: ["GET"],
		requestBody: "buffered",
		fetch: (request) => handle(ctx, request),
	});
}
