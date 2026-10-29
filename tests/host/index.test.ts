/**
 * 能力探测与 RPC 信封测试（§1 第 4 条：缺依赖给明确诊断，不是静默失败）。
 */

import { describe, expect, it } from 'vitest';
import { Context } from '@deepseek-ai/cordis';
import { detectCapabilities } from '@/host/capabilities';
import { createHandler } from '@/host/rpc';
import { WorkbenchService } from '@/host/workbench-service';
import { AgentWorkflow } from '@/host/agent-workflow';
import type { HostContext } from '@/host/types';

describe('detectCapabilities（逐项诊断）', () => {
  it('服务全缺时宿主服务类 items ok=false，diagnostics 非空（明确诊断）', () => {
    const snapshot = detectCapabilities({}, { pushEnabled: false });
    expect(snapshot.items.length).toBeGreaterThan(0);
    // 宿主服务类（rpc/skills/agent/effect）全缺时必须 ok=false；解释器/数据根不在此列。
    for (const key of ['rpc', 'skills', 'agent', 'effect']) {
      expect(snapshot.items.find((i) => i.key === key)!.ok).toBe(false);
    }
    expect(snapshot.diagnostics.length).toBeGreaterThan(0);
    expect(snapshot.read).toBe(false);
    expect(snapshot.agent).toBe(false);
  });

  it('能力探测包含解释器路径/版本与数据根（WEWRITE_HOME）两项', () => {
    const snapshot = detectCapabilities({}, { pushEnabled: false });
    const interpreter = snapshot.items.find((i) => i.key === 'interpreter');
    const dataRoot = snapshot.items.find((i) => i.key === 'dataRoot');
    expect(interpreter).toBeDefined();
    expect(dataRoot).toBeDefined();
    expect(interpreter!.detail.length).toBeGreaterThan(0);
    expect(dataRoot!.detail.length).toBeGreaterThan(0);
  });

  it('服务齐全时 read/agent/skills 可用，push 默认关闭', () => {
    const services: Record<string, unknown> = {
      connection: { admit: () => ({ peer: {} }) },
      skills: { registerProvider: () => () => undefined },
      agents: { create: async () => ({}) },
      approval: { request: async () => ({}) },
    };
    const ctx: HostContext = { get: (name: string) => services[name], effect: () => undefined };
    const snapshot = detectCapabilities(ctx, { pushEnabled: false });
    expect(snapshot.read).toBe(true);
    expect(snapshot.agent).toBe(true);
    expect(snapshot.skills).toBe(true);
    expect(snapshot.push).toBe(false);
  });

  it('pushEnabled=true 且审批服务就绪时 push 可用', () => {
    const services: Record<string, unknown> = {
      connection: { admit: () => ({ peer: {} }) },
      approval: { request: async () => ({}) },
    };
    const ctx: HostContext = { get: (name: string) => services[name] };
    expect(detectCapabilities(ctx, { pushEnabled: true }).push).toBe(true);
    expect(detectCapabilities(ctx, { pushEnabled: false }).push).toBe(false);
  });
});

describe('生命周期：真实 Cordis Context 的 ctx.effect 语义', () => {
  it('body 返回 disposer → 装载时不执行清理，卸载时逆序执行', async () => {
    const ctx = new Context();
    const log: string[] = [];
    const disposers = [
      () => { log.push('dispose B'); },
      () => { log.push('dispose A'); },
    ];
    // 正确形态：body 返回清理函数。
    ctx.effect(() => async () => {
      for (const d of [...disposers].reverse()) await Promise.resolve(d()).catch(() => undefined);
    }, 'right');
    expect(log).toEqual([]);

    // 卸载 fiber（dispose 返回的函数被执行）
    await ctx.fiber.dispose();
    expect(log).toEqual(['dispose A', 'dispose B']);
  });

  it('body 直接执行清理（错误形态）→ 装载时即执行，用于防止回归', async () => {
    const ctx = new Context();
    const log: string[] = [];
    const disposers = [
      () => { log.push('dispose B'); },
      () => { log.push('dispose A'); },
    ];
    // 错误形态：body 被立即执行，清理动作在装载阶段就跑掉了（类型上返回 void，故用 never 绕过）。
    ctx.effect((async () => {
      for (const d of [...disposers].reverse()) await Promise.resolve(d()).catch(() => undefined);
    }) as never, 'wrong');
    expect(log).toEqual(['dispose A']);
    await ctx.fiber.dispose();
  });
});

describe('createHandler（RPC 信封契约）', () => {
  function makeService(): WorkbenchService {
    const workflow = new AgentWorkflow({
      runtime: {
        create: async () => { throw new Error('agents 缺失'); },
        resume: async () => { throw new Error('agents 缺失'); },
        get: () => undefined,
      },
      bridge: async () => ({ ok: false, error: { code: 'INTERNAL', message: '内部错误', retryable: false } }),
      homeLabel: '数据根未配置',
      mainSkillName: 'wewrite',
      randomId: () => 'id-1',
    });
    return new WorkbenchService({
      bridge: async (operation) => {
        if (operation === 'snapshot') {
          return {
            ok: true,
            data: {
              runs: [],
              nextCursor: null,
              themes: [],
              capabilities: { read: true, write: false, agent: false, skills: false, preview: false, push: false },
              diagnostics: [],
              settings: { visualMode: 'none', pushEnabled: false, publishTransport: 'fake', credentialConfigured: false },
            },
          };
        }
        return { ok: false, error: { code: 'CAPABILITY_UNAVAILABLE', message: '能力不可用', retryable: false } };
      },
      workflow,
      randomId: () => 'id-1',
    });
  }

  it('snapshot 合法请求 → 传输 value 为业务联合 {ok:true,data}', async () => {
    const handler = createHandler(makeService());
    const result = (await handler('snapshot', {}, new AbortController().signal, {})) as {
      ok: boolean;
      value?: { ok: boolean; data?: unknown };
    };
    expect(result.ok).toBe(true);
    expect(result.value?.ok).toBe(true);
    expect(result.value?.data).toBeDefined();
  });

  it('snapshot 未知字段 → {ok:false,error}，code=internal 且 message 中文不含栈', async () => {
    const handler = createHandler(makeService());
    const result = (await handler('snapshot', { __extra: 1 }, new AbortController().signal, {})) as {
      ok: boolean;
      error?: { code: string; message: string };
    };
    expect(result.ok).toBe(false);
    expect(result.error?.code).toBe('internal');
    expect(result.error?.message).toContain('校验');
    expect(result.error?.message).not.toContain('at ');
  });

  it('run/detail 端点已接线（契约校验通过后走 service）', async () => {
    const handler = createHandler(makeService());
    const result = (await handler(
      'run/detail',
      { runId: '20261028-120000-abcdef' },
      new AbortController().signal,
      {},
    )) as { ok: boolean; error?: { message: string } };
    // 业务失败在传输 value 内（ok:true），不是宿主传输失败。
    expect(result.ok).toBe(true);
  });

  it('未知端点 → 未知端点错误', async () => {
    const handler = createHandler(makeService());
    const result = (await handler('nope', {}, new AbortController().signal, {})) as {
      ok: boolean;
      error?: { message: string };
    };
    expect(result.ok).toBe(false);
    expect(result.error?.message).toContain('未知端点');
  });
});
