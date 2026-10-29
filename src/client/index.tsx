/**
 * dsh-plugin-wewrite 客户端入口（S3：真实工作台面板 + 宿主 RPC 接线）。
 * 三个入口：conversation.view（会话内 tab）/ sidebar.footer.action（侧栏 footer）/ shell.overlay（全屏浮层）。
 *
 * rc.4 实测：
 * - slots.register() 只对「已声明」的 slot 生效，未声明直接抛错；正确形态是 ctx.slots.inject()。
 * - RPC 走宿主真实通道 ctx.connection.rpc.call(channel, endpoint, payload, signal?)（ClientConnectionRpc
 *   的正式方法），connection 服务由 dsh-client-connection 的 client half 提供。
 */

import { useMemo, useState } from 'react';
import type { ClientContext, ClientRuntime, ClientSlotOptions } from './context';
import { WeWriteClient } from './rpc';
import { RunsPanel } from './panels/Runs';
import { TopicsPanel } from './panels/Topics';
import { EditorPanel } from './panels/Editor';
import { ReviewPanel } from './panels/Review';
import { SettingsPanel } from './panels/Settings';

export const name = 'dsh-plugin-wewrite-client';
export const inject = ['slots'];

const PANEL_TITLE = 'WeWrite 写作台';

/** 启动自检错误收集：任何入口位注册失败/未声明都会被这里记录，供诊断快照/启动自检读取。 */
export const bootDiagnostics: Array<{ slot: string; message: string }> = [];

function recordFailure(slot: string, error: unknown): void {
  const message = error instanceof Error ? error.message : String(error);
  bootDiagnostics.push({ slot, message });
  console.warn(`[dsh-plugin-wewrite] 入口位 ${slot} 注册失败（启动自检可读）`, message);
}

function recordPending(slot: string): void {
  bootDiagnostics.push({ slot, message: '入口位尚未被宿主声明，等待声明后再注册' });
}

type ViewKey = 'runs' | 'topics' | 'editor' | 'review' | 'settings';

/** 「写这个」完整两跳流程（返修 2 可判定单元）：runCreate 成功后必须 runStart，成功才交付 runId。 */
export async function startWriting(
  topic: string,
  themeId: string,
  workspaceId: string | null,
  client: WeWriteClient,
  randomId: () => string,
): Promise<{ ok: true; runId: string } | { ok: false; message: string }> {
  const created = await client.runCreate({
    requestId: randomId(),
    topic,
    mode: 'draft',
    visualMode: 'none',
    themeId,
  });
  if (!created.ok) {
    return { ok: false, message: created.error.message };
  }
  const { runId } = created.data as { runId: string };

  if (!workspaceId) {
    return { ok: false, message: '当前没有可用工作区，无法启动写作会话' };
  }
  const started = await client.runStart({
    requestId: randomId(),
    runId,
    workspaceId,
  });
  if (!started.ok) {
    return { ok: false, message: started.error.message };
  }
  return { ok: true, runId };
}

/** 从 ctx.workspaces 解析当前工作区 id（首个可用工作区；无则 null）。 */
function resolveWorkspaceId(ctx: ClientContext): string | null {
  const workspaces = ctx.workspaces;
  if (!workspaces) return null;
  try {
    const items = workspaces.list.getSnapshot().items;
    if (items.length > 0) return items[0].workspaceId;
  } catch {
    // 忽略：工作区服务未就绪时 workspaceId=null，run/start 会因缺 workspaceId 拒绝。
  }
  return null;
}

/** 真实工作台面板：任务列表/选题/编辑器/审稿/设置。 */
function WorkbenchPanel({ runtime }: { runtime: ClientRuntime }): JSX.Element {
  const [view, setView] = useState<ViewKey>('runs');
  const [runId, setRunId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const client = useMemo(() => new WeWriteClient(runtime.connection), [runtime.connection]);

  return (
    <div className="ww-workbench" data-testid="wewrite-workbench">
      <nav className="ww-workbench__nav">
        {([['runs', '任务'], ['topics', '选题'], ['editor', '编辑器'], ['review', '审稿'], ['settings', '设置']] as [ViewKey, string][]).map(([key, label]) => (
          <button
            key={key}
            className={view === key ? 'is-active' : ''}
            onClick={() => setView(key)}
            disabled={key === 'editor' || key === 'review' ? !runId : false}
            data-testid={`ww-nav-${key}`}
          >
            {label}
          </button>
        ))}
      </nav>
      {actionError ? (
        <div className="ww-state ww-state--error" data-testid="ww-action-error">{actionError}</div>
      ) : null}
      <div className="ww-workbench__body">
        {view === 'runs' && <RunsPanel client={client} onSelect={setRunId} />}
        {view === 'topics' && (
          <TopicsPanel
            client={client}
            onWrite={async (topic, themeId) => {
              setActionError(null);
              const result = await startWriting(topic, themeId, runtime.workspaceId, client, () => crypto.randomUUID());
              if (!result.ok) {
                setActionError(result.message);
                return;
              }
              setRunId(result.runId);
              setView('runs');
            }}
          />
        )}
        {view === 'editor' && runId && <EditorPanel client={client} runId={runId} />}
        {view === 'review' && runId && <ReviewPanel client={client} runId={runId} />}
        {view === 'settings' && <SettingsPanel client={client} />}
      </div>
    </div>
  );
}

function ConversationView({ runtime }: { runtime: ClientRuntime }): JSX.Element {
  return <WorkbenchPanel runtime={runtime} />;
}

function SidebarFooterAction({ wide, runtime }: { wide: boolean; runtime: ClientRuntime }): JSX.Element {
  void wide;
  return <WorkbenchPanel runtime={runtime} />;
}

function ShellOverlay({ runtime }: { runtime: ClientRuntime }): JSX.Element {
  return <WorkbenchPanel runtime={runtime} />;
}

/** 三个入口位的注册选项。conversation.view 的 inject 返回视图 props（session 相关，占位为最小面）。 */
function slotOptions(slot: ClientSlotOptions['name']): ClientSlotOptions {
  switch (slot) {
    case 'conversation.view':
      return {
        name: 'conversation.view',
        id: 'wewrite',
        order: 50,
        label: () => PANEL_TITLE,
        inject: (sessionId: unknown) => ({ sessionId }),
      };
    case 'sidebar.footer.action':
      return { name: 'sidebar.footer.action', id: 'wewrite', order: 100, label: () => PANEL_TITLE };
    case 'shell.overlay':
      return { name: 'shell.overlay', id: 'wewrite', order: 100, label: () => PANEL_TITLE };
  }
}

function slotComponent(slot: ClientSlotOptions['name']): unknown {
  switch (slot) {
    case 'conversation.view':
      return ConversationView;
    case 'sidebar.footer.action':
      return SidebarFooterAction;
    case 'shell.overlay':
      return ShellOverlay;
  }
}

export function apply(ctx: ClientContext): void {
  const slots = ctx.slots;
  if (!slots) {
    console.warn('[dsh-plugin-wewrite] slots 服务缺失，入口位不可用');
    return;
  }

  // 返修 1：RPC 走宿主真实通道 ctx.connection.rpc.call（不再使用无赋值点的 globalThis.__wewriteRpc）。
  const runtime: ClientRuntime = {
    connection: ctx.connection ?? { rpc: { call: async () => ({ ok: false, error: { code: 'CAPABILITY_UNAVAILABLE', message: 'RPC 通道不可用', details: {} } }) } },
    workspaceId: resolveWorkspaceId(ctx),
  };

  // 收集每个 slot 的 wait disposer（ctx.slots.inject 返回）与 register disposer。
  const waitDisposers: Array<() => void> = [];
  const registerDisposers: Array<() => void> = [];
  const registered = new Set<string>();

  for (const slot of ['conversation.view', 'sidebar.footer.action', 'shell.overlay'] as const) {
    if (typeof slots.inject === 'function') {
      try {
        const wait = slots.inject(slot, () => {
          const component = slotComponent(slot) as (props: { runtime: ClientRuntime } & Record<string, unknown>) => JSX.Element;
          const dispose = slots.register(
            { ...slotOptions(slot), inject: (...args: unknown[]) => ({ runtime, ...(slot === 'conversation.view' ? { sessionId: args[0] } : {}) }) },
            component,
          );
          registerDisposers.push(dispose);
          registered.add(slot);
          return dispose;
        });
        waitDisposers.push(wait);
      } catch (error) {
        try {
          const component = slotComponent(slot) as (props: { runtime: ClientRuntime } & Record<string, unknown>) => JSX.Element;
          const dispose = slots.register(
            { ...slotOptions(slot), inject: (...args: unknown[]) => ({ runtime, ...(slot === 'conversation.view' ? { sessionId: args[0] } : {}) }) },
            component,
          );
          registerDisposers.push(dispose);
          registered.add(slot);
        } catch (registerError) {
          recordFailure(slot, registerError);
        }
      }
    } else {
      try {
        const component = slotComponent(slot) as (props: { runtime: ClientRuntime } & Record<string, unknown>) => JSX.Element;
        const dispose = slots.register({ ...slotOptions(slot), inject: (...args: unknown[]) => ({ runtime, ...(slot === 'conversation.view' ? { sessionId: args[0] } : {}) }) }, component);
        registerDisposers.push(dispose);
        registered.add(slot);
      } catch (error) {
        recordFailure(slot, error);
      }
    }
  }

  // 未注册且无失败记录的入口位：记为 pending（未声明），供启动自检读取。
  for (const slot of ['conversation.view', 'sidebar.footer.action', 'shell.overlay'] as const) {
    if (!registered.has(slot) && !bootDiagnostics.some((d) => d.slot === slot)) {
      recordPending(slot);
    }
  }

  ctx.effect?.(() => () => {
    for (const dispose of [...registerDisposers].reverse()) dispose();
    for (const wait of [...waitDisposers].reverse()) wait();
  }, 'wewrite-client');
}
