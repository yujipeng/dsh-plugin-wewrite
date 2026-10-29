/**
 * 客户端入口位类型（S1 占位面 → S3 真实接线）。
 * 形状对标本机 DSH 0.2.1-rc.4 的 ctx.slots / ctx.connection / ctx.workspaces。
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

/** 宿主 Connection client 面（dsh-client-connection rc.4 ClientConnectionRpc）。 */
export interface ClientConnection {
  readonly rpc: {
    call(channel: string, endpoint: string, payload: unknown, signal?: AbortSignal): Promise<{
      readonly ok: boolean;
      readonly value?: unknown;
      readonly error?: { readonly code: string; readonly message: string; readonly details: object };
    }>;
  };
}

/** 宿主 Workspaces client 面（dsh-api-workspace-controller rc.4 IWorkspaces）。 */
export interface ClientWorkspaces {
  readonly list: {
    getSnapshot(): {
      readonly items: ReadonlyArray<{ readonly workspaceId: string; readonly path: string; readonly title: string }>;
    };
  };
}

export interface ClientContext {
  readonly slots?: ClientSlots;
  readonly connection?: ClientConnection;
  readonly workspaces?: ClientWorkspaces;
  readonly effect?: (factory: () => void | (() => void), label?: string) => void;
}

/** 当前选中工作区 id（由 apply 时从 ctx.workspaces 解析后写入，供面板发起 run/start 使用）。 */
export interface ClientRuntime {
  readonly connection: ClientConnection;
  readonly workspaceId: string | null;
}
