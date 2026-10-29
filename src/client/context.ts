/**
 * 客户端入口位类型（S1 占位面）。真实面板属后续 stage。
 * 形状对标本机 DSH 0.2.1-rc.4 的 ctx.slots（SlotRegistry）最小消费面。
 */

/** 注册一个入口位：rc.4 只对「已声明」的 slot 生效，未声明直接抛错。 */
export interface ClientSlotOptions {
  readonly name: 'conversation.view' | 'sidebar.footer.action' | 'shell.overlay';
  readonly id?: string;
  readonly order?: number;
  readonly label?: string | (() => string);
  readonly inject?: (...args: unknown[]) => unknown;
}

export interface ClientSlots {
  /** 注册入口位，返回 idempotent disposer。 */
  register(options: ClientSlotOptions, component: unknown): () => void;
  /** 等 slot 声明后再执行 callback（rc.4 正确形态），返回 wait disposer。 */
  inject?(slot: ClientSlotOptions['name'], callback: () => () => void): () => void;
}

export interface ClientContext {
  readonly slots?: ClientSlots;
  readonly effect?: (factory: () => void | (() => void), label?: string) => void;
}

/**
 * 宿主注入的 RPC 请求通道（dsh-client-connection 的 Web client 面）。
 * 由 slot inject 在 conversation.view 注入时挂到 globalThis，供面板发起 RPC。
 * 仅浏览器端存在；SSR/测试无此全局时面板显示「RPC 通道不可用」。
 */
declare global {
  // eslint-disable-next-line no-var
  var __wewriteRpc:
    | ((channel: string, endpoint: string, payload: unknown) => Promise<unknown>)
    | undefined;
}
