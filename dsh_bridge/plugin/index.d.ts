/** Loopback route that hands a local tool the current authenticated Web URL. */
import type { Context } from "@deepseek-ai/cordis";

export declare const name: "dsh-local-bridge";
export declare const inject: string[];

/** Absolute path on the shared `/api` channel. */
export declare const BRIDGE_PATH: "/local-bridge/auth";

/** 0600 file holding the per-boot shared secret. */
declare const SECRET_PATH: string;

/** Host headers accepted as loopback. */
declare const LOOPBACK: Set<string>;

declare function mintSecret(): string;
declare function persistSecret(value: string): void;
declare function isLoopback(request: Request): boolean;
declare function json(body: object, status?: number): Response;
declare function handle(ctx: Context, request: Request): Response;

export declare function apply(ctx: Context): void;
