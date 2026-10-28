/**
 * 客户端入口位类型（S1 占位面）。真实面板属后续 stage。
 */

export interface ClientSlotOptions {
  readonly name: 'conversation.view' | 'sidebar.footer.action' | 'shell.overlay';
  readonly id?: string;
  readonly order?: number;
  readonly locale?: string;
  readonly label?: string | (() => string);
  readonly inject?: (...args: unknown[]) => unknown;
}

export interface ClientContext {
  readonly slots?: {
    register(options: ClientSlotOptions, component: unknown): () => void;
    inject?(key: string, callback: () => () => void): () => void;
  };
  readonly effect?: (factory: () => void | (() => void), label?: string) => void;
}
