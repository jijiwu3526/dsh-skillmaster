/** Loopback route that hands a local tool the current authenticated Web URL. */
import type { Context } from "@deepseek-ai/cordis";
import type { IncomingMessage, ServerResponse } from "node:http";

export declare const name: "dsh-local-bridge";
export declare const inject: ["connection", "webServer"];

/**
 * Absolute route path. Deliberately OUTSIDE `/api`.
 *
 * DSH authenticates the whole `/api` channel before dispatching to any route
 * on it, so a bridge mounted there is unreachable by the very unauthenticated
 * caller it serves — a chicken-and-egg problem. The plugin registers via
 * `ctx.webServer.register()` to sit beside `/api` instead.
 */
export declare const BRIDGE_PATH: "/local-bridge/auth";

/** Absolute path of the 0600 file holding the per-boot shared secret. */
declare function secretPath(): string;

/** Host headers accepted as loopback. */
declare const LOOPBACK: Set<string>;

declare function mintSecret(): string;
declare function persistSecret(value: string): void;
declare function isLoopback(request: IncomingMessage): boolean;
declare function send(
	response: ServerResponse,
	status: number,
	body: Record<string, unknown>,
): void;

export declare function apply(ctx: Context): void;
