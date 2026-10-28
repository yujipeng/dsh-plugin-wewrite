/**
 * 客户端入口位测试（rc.4 slots 语义）：
 * - 用真实 SlotCore（@deepseek-ai/dsh-client-ui-slots）承载 register 语义（未声明抛错、entries 追踪）。
 * - 用忠实复刻 rc.4 SlotRegistry.inject 的 wait 包装，等 slot 声明后再 register。
 * 断言：三个入口位在声明后注册成功、disposer 可回收；未声明时不得静默（bootDiagnostics 有记录）。
 */

import { describe, expect, it } from 'vitest';
import { SlotCore } from '@deepseek-ai/dsh-client-ui-slots';
import { apply, bootDiagnostics } from '@/client/index';
import type { ClientContext } from '@/client/context';

/** 用 SlotCore 承载注册语义，忠实复刻 rc.4 SlotRegistry.inject（声明后回调/等待声明）。 */
class Rc4SlotsLike {
  readonly core = new SlotCore();
  private readonly declarationWaiters = new Map<string, Set<() => void>>();

  /** 模拟宿主 shell 父入口在 root 上一次性声明三个目标 slot（children 表）。 */
  declareTargets(): void {
    const keys = ['conversation.view', 'sidebar.footer.action', 'shell.overlay'];
    const children: Record<string, { kind: 'list'; scope: 'session' | 'root' }> = {
      'conversation.view': { kind: 'list', scope: 'session' },
      'sidebar.footer.action': { kind: 'list', scope: 'root' },
      'shell.overlay': { kind: 'list', scope: 'root' },
    };
    // 项目内 SlotMap 未做声明合并，keyof SlotMap = never；用 as never 绕过类型层，仅测运行语义。
    this.core.register({ name: 'root', children } as never, (() => null) as never);
    for (const key of keys) {
      const waiters = this.declarationWaiters.get(key);
      if (waiters) {
        for (const run of [...waiters]) run();
        waiters.clear();
      }
    }
  }

  register(options: { name: string; id?: string; order?: number; label?: unknown; inject?: unknown }, component: unknown): () => void {
    const dispose = this.core.register(options as never, component as never);
    return () => dispose();
  }

  inject(key: string, callback: () => () => void): () => void {
    if (this.core.specDynamic(key) !== undefined) {
      const dispose = callback();
      return () => dispose();
    }
    let stopped = false;
    const run = () => {
      if (stopped) return;
      stopped = true;
      callback();
    };
    let waiters = this.declarationWaiters.get(key);
    if (!waiters) {
      waiters = new Set();
      this.declarationWaiters.set(key, waiters);
    }
    waiters.add(run);
    return () => {
      stopped = true;
      waiters?.delete(run);
    };
  }
}

function makeContext(slots: Rc4SlotsLike): { ctx: ClientContext; disposers: Array<() => void> } {
  const disposers: Array<() => void> = [];
  return {
    ctx: {
      slots: {
        register: (options, component) => slots.register(options, component),
        inject: (slot, callback) => slots.inject(slot, callback),
      },
      effect: (factory) => {
        const cleanup = factory();
        if (typeof cleanup === 'function') disposers.push(cleanup);
        return undefined;
      },
    },
    disposers,
  };
}

describe('client 入口位（rc.4 slots 服务）', () => {
  it('三个 slot 声明后经 inject 注册成功，disposer 可回收', () => {
    bootDiagnostics.length = 0;
    const slots = new Rc4SlotsLike();
    slots.declareTargets();

    const { ctx, disposers } = makeContext(slots);
    apply(ctx);

    expect(slots.core.entries('conversation.view').length).toBe(1);
    expect(slots.core.entries('sidebar.footer.action').length).toBe(1);
    expect(slots.core.entries('shell.overlay').length).toBe(1);

    // 无失败记录（非静默反证）
    expect(bootDiagnostics.filter((d) => d.message.includes('失败'))).toEqual([]);

    // 卸载：disposer 回收，entries 清空
    for (const dispose of [...disposers].reverse()) dispose();
    expect(slots.core.entries('conversation.view').length).toBe(0);
    expect(slots.core.entries('sidebar.footer.action').length).toBe(0);
    expect(slots.core.entries('shell.overlay').length).toBe(0);
  });

  it('先 apply 再声明 slot：inject 等待声明后注册成功（不静默）', () => {
    bootDiagnostics.length = 0;
    const slots = new Rc4SlotsLike();
    const { ctx } = makeContext(slots);
    apply(ctx);

    expect(slots.core.entries('conversation.view').length).toBe(0);
    expect(bootDiagnostics.some((d) => d.slot === 'conversation.view' && d.message.includes('尚未被宿主声明'))).toBe(true);

    slots.declareTargets();

    expect(slots.core.entries('conversation.view').length).toBe(1);
    expect(slots.core.entries('sidebar.footer.action').length).toBe(1);
    expect(slots.core.entries('shell.overlay').length).toBe(1);
  });

  it('未声明且从未声明：pending 记录存在（不得静默）', () => {
    bootDiagnostics.length = 0;
    const slots = new Rc4SlotsLike();
    const { ctx } = makeContext(slots);
    apply(ctx);
    expect(slots.core.entries('conversation.view').length).toBe(0);
    expect(bootDiagnostics.some((d) => d.slot === 'conversation.view' && d.message.includes('尚未被宿主声明'))).toBe(true);
    expect(bootDiagnostics.some((d) => d.slot === 'sidebar.footer.action' && d.message.includes('尚未被宿主声明'))).toBe(true);
    expect(bootDiagnostics.some((d) => d.slot === 'shell.overlay' && d.message.includes('尚未被宿主声明'))).toBe(true);
  });
});
