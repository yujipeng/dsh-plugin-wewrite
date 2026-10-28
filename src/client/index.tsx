/**
 * dsh-plugin-wewrite 客户端入口（S1：仅注册入口位并渲染占位面板，验证装载链路通）。
 * 三个入口：conversation.view（会话内 tab）/ sidebar.footer.action（侧栏 footer）/ shell.overlay（全屏浮层）。
 * 真实工作台面板属后续 stage。
 */

import { useEffect, useState } from 'react';
import type { ClientContext } from './context';

export const name = 'dsh-plugin-wewrite-client';
export const inject = ['slots'];

const PANEL_TITLE = 'WeWrite 写作台';

function warnDegraded(action: string, error: unknown): void {
  console.warn(`[dsh-plugin-wewrite] ${action} 失败，已降级跳过`, error);
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

export function apply(ctx: ClientContext): void {
  const slots = ctx.slots;
  if (!slots) {
    warnDegraded('slots 服务缺失', 'slots unavailable');
    return;
  }

  const disposers: Array<() => void> = [];

  const registerSlot = (slot: ClientSlotName): (() => void) | undefined => {
    try {
      if (slot === 'conversation.view') {
        return slots.register(
          {
            name: 'conversation.view',
            id: 'wewrite',
            order: 50,
            locale: 'zh',
            label: () => PANEL_TITLE,
            inject: (..._args: unknown[]) => ({}),
          },
          ConversationView,
        );
      }
      if (slot === 'sidebar.footer.action') {
        return slots.register(
          { name: 'sidebar.footer.action', id: 'wewrite', order: 100, label: () => PANEL_TITLE },
          SidebarFooterAction,
        );
      }
      return slots.register(
        { name: 'shell.overlay', id: 'wewrite', order: 100, label: () => PANEL_TITLE },
        ShellOverlay,
      );
    } catch (error) {
      warnDegraded(`slots.register（${slot}）`, error);
      return undefined;
    }
  };

  for (const slot of ['conversation.view', 'sidebar.footer.action', 'shell.overlay'] as const) {
    const dispose = registerSlot(slot);
    if (dispose) disposers.push(dispose);
  }

  ctx.effect?.(() => () => {
    for (const dispose of [...disposers].reverse()) dispose();
  }, 'wewrite-client');
}

type ClientSlotName = 'conversation.view' | 'sidebar.footer.action' | 'shell.overlay';
