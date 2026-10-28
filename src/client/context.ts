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
