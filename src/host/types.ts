/**
 * DSH 宿主服务的最小接口声明（对标本机 DSH 0.2.1-rc.4 实测面）。
 * 业务模块只依赖这些结构面——测试不打真 DSH 的前提（宿主类型与业务逻辑解耦）。
 * 全部服务在 apply 内做存在性探测，缺失时降级为明确诊断（§9.1），不是静默失败。
 */

import type { ErrorCode } from '../shared/constants';

// ── 宿主 RPC 信封（dsh-client-connection rc.4 实测）─────────────────────

export interface ConnectionRpcFailure {
  readonly code: string;
  readonly message: string;
  readonly details: object;
}

export type ConnectionRpcResult<T = unknown> =
  | { readonly ok: true; readonly value: T }
  | { readonly ok: false; readonly error: ConnectionRpcFailure };

/** Peer 的调用身份（§4：只接受已认证 owner 浏览器/所属工作区）。 */
export interface PrincipalLike {
  readonly kind?: string;
}

export interface PeerScopeLike {
  readonly id?: unknown;
  readonly principal?: PrincipalLike;
  readonly signal?: AbortSignal;
}

export type RpcHandler = (
  endpoint: string,
  payload: unknown,
  signal: AbortSignal,
  peer: PeerScopeLike,
) => Promise<ConnectionRpcResult>;

export interface ConnectionRpcService {
  handle(channel: string, handler: RpcHandler): () => Promise<void>;
}

export interface ConnectionService {
  readonly rpc?: ConnectionRpcService;
}

// ── 日志 ────────────────────────────────────────────────────────────────

export interface HostLogger {
  info(message: string): void;
  warn(message: string): void;
  error(message: string): void;
}

// ── 生命周期 ─────────────────────────────────────────────────────────────

export type Disposer = () => void | Promise<void>;

export interface HostContext {
  /** 通过 ctx.get 无副作用读取任意服务（不声明 inject 也不阻塞）。 */
  readonly get?: (name: string) => unknown;
  /** 起一个带依赖的子 fiber，服务到位后回调（rc.4 官方装配模式）。 */
  readonly inject?: (deps: readonly string[], callback: (ctx: HostContext) => void) => unknown;
  readonly effect?: (body: () => Disposer | Promise<Disposer | void> | void, label?: string) => unknown;
  readonly logger?: HostLogger | ((name: string) => HostLogger);
}

export function resolveLogger(ctx: HostContext, name: string): HostLogger {
  const candidate = typeof ctx.logger === 'function' ? ctx.logger(name) : ctx.logger;
  if (candidate) return candidate;
  return {
    info: (message) => console.log(`[${name}] ${message}`),
    warn: (message) => console.warn(`[${name}] ${message}`),
    error: (message) => console.error(`[${name}] ${message}`),
  };
}

/** 统一格式化的业务错误（无栈、无 secret）。 */
export class WeWriteError extends Error {
  readonly code: ErrorCode;
  readonly retryable: boolean;
  readonly runId?: string;

  constructor(code: ErrorCode, message: string, options?: { retryable?: boolean; runId?: string }) {
    super(message);
    this.name = 'WeWriteError';
    this.code = code;
    this.retryable = options?.retryable ?? false;
    this.runId = options?.runId;
  }
}
