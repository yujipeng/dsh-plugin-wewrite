/**
 * dsh-plugin-wewrite 客户端入口（S1：仅注册入口位并渲染占位面板，验证装载链路通）。
 * 三个入口：conversation.view（会话内 tab）/ sidebar.footer.action（侧栏 footer）/ shell.overlay（全屏浮层）。
 *
 * rc.4 实测：slots.register() 只对「已声明」的 slot 生效，未声明直接抛错；
 * 正确形态是用 ctx.slots.inject() 包裹，等 slot 声明后再注册（与 dsh-client-ui-chat/
 * dsh-client-ui-cordis 官方范本一致）。裸 register 会把「未声明」错误吞进降级路径，
 * 页面看起来正常但入口位静默消失——这是质检与页面取证共同暴露的缺陷。
 */

import { useEffect, useState } from 'react';
import type { ClientContext, ClientSlotOptions } from './context';

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

/** 占位面板：证明入口位渲染成功，后续 stage 替换为真实面板。 */
function PlaceholderPanel({ wide }: { wide?: boolean }): JSX.Element {
  const [ready, setReady] = useState(false);
  useEffect(() => {
    setReady(true);
  }, []);
  return (
    <div
      className="ww-placeholder"
      data-testid="wewrite-placeholder"
      aria-label={PANEL_TITLE}
    >
      <span className="ww-placeholder__title">{PANEL_TITLE}</span>
      <span className="ww-placeholder__hint">工程骨架已装载（S1 占位，真实面板属后续 stage）</span>
      <span className="ww-placeholder__ready">{ready ? 'loaded' : 'mounting'}</span>
      {wide !== undefined ? <span className="ww-placeholder__wide">{wide ? 'wide' : 'rail'}</span> : null}
    </div>
  );
}

function ConversationView(): JSX.Element {
  return <PlaceholderPanel />;
}

function SidebarFooterAction({ wide }: { wide: boolean }): JSX.Element {
  return <PlaceholderPanel wide={wide} />;
}

function ShellOverlay(): JSX.Element {
  return <PlaceholderPanel />;
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

  // 收集每个 slot 的 wait disposer（ctx.slots.inject 返回）与 register disposer。
  const waitDisposers: Array<() => void> = [];
  const registerDisposers: Array<() => void> = [];
  const registered = new Set<string>();

  for (const slot of ['conversation.view', 'sidebar.footer.action', 'shell.overlay'] as const) {
    if (typeof slots.inject === 'function') {
      try {
        const wait = slots.inject(slot, () => {
          const dispose = slots.register(slotOptions(slot), slotComponent(slot));
          registerDisposers.push(dispose);
          registered.add(slot);
          return dispose;
        });
        waitDisposers.push(wait);
        // 若 slot 此刻尚未声明，inject 会等待声明；记录 pending 供自检（不视为失败）。
        // 注意：rc.4 SlotRegistry.inject 在 slot 已声明时同步回调并返回 wait；未声明时仅登记等待。
        // 无法同步区分二者，故 pending 记录仅在「未注册且无错误」时于 effect 卸载前统一结算。
      } catch (error) {
        try {
          const dispose = slots.register(slotOptions(slot), slotComponent(slot));
          registerDisposers.push(dispose);
          registered.add(slot);
        } catch (registerError) {
          recordFailure(slot, registerError);
        }
      }
    } else {
      try {
        const dispose = slots.register(slotOptions(slot), slotComponent(slot));
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
