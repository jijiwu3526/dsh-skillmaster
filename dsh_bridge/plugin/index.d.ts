/** Loopback route that hands a local tool the current authenticated Web URL. */
import type { Context } from "@deepseek-ai/cordis";

export declare const name: "dsh-local-bridge";
export declare const inject: string[];

/**
 * Absolute route path. Deliberately OUTSIDE `/api`.
 *
 * DSH authenticates the whole `/api` channel before dispatching, so a bridge
 * mounted there is unreachable by the very unauthenticated caller it serves.
 * The plugin registers via `ctx.webServer.register()` to sit beside `/api`.
 */
export declare const BRIDGE_PATH: "/local-bridge/auth";

/** 0600 file holding the per-boot shared secret. */
declare const SECRET_PATH: string;

/** Host headers accepted as loopback. */
declare const LOOPBACK: Set<string>;

declare function mintSecret(): string;
declare function persistSecret(value: string): void;
declare function isLoopback(request: Request): boolean;
declare function send(
	response: unknown,
	status: number,
	body: Record<string, unknown>,
): void;

export declare function apply(ctx: Context): void;
