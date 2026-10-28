/**
 * 能力探测与 RPC 信封测试（§1 第 4 条：缺依赖给明确诊断，不是静默失败）。
 */

import { describe, expect, it, vi } from 'vitest';
import { detectCapabilities } from '@/host/capabilities';
import { createHandler } from '@/host/rpc';
import type { HostContext } from '@/host/types';

describe('detectCapabilities（逐项诊断）', () => {
  it('服务全缺时逐项 ok=false，diagnostics 非空（明确诊断）', () => {
    const snapshot = detectCapabilities({}, { pushEnabled: false });
    expect(snapshot.items.length).toBeGreaterThan(0);
    expect(snapshot.items.every((item) => item.ok === false)).toBe(true);
    expect(snapshot.diagnostics.length).toBeGreaterThan(0);
    expect(snapshot.read).toBe(false);
    expect(snapshot.agent).toBe(false);
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

describe('createHandler（RPC 信封契约）', () => {
  const snapshotService = {
    snapshot: vi.fn(async () => ({
      runs: [],
      nextCursor: null,
      themes: [],
      capabilities: { read: true, write: false, agent: false, skills: false, preview: false, push: false },
      diagnostics: [],
      settings: { visualMode: 'none', pushEnabled: false, publishTransport: 'fake', credentialConfigured: false },
    })),
  };

  it('snapshot 合法请求 → {ok:true,value}', async () => {
    const handler = createHandler(snapshotService);
    const result = (await handler('snapshot', {}, new AbortController().signal, {})) as {
      ok: boolean;
      value?: unknown;
    };
    expect(result.ok).toBe(true);
    expect(result.value).toBeDefined();
  });

  it('snapshot 未知字段 → {ok:false,error}，code=internal 且 message 中文不含栈', async () => {
    const handler = createHandler(snapshotService);
    const result = (await handler('snapshot', { __extra: 1 }, new AbortController().signal, {})) as {
      ok: boolean;
      error?: { code: string; message: string };
    };
    expect(result.ok).toBe(false);
    expect(result.error?.code).toBe('internal');
    expect(result.error?.message).toContain('校验');
    expect(result.error?.message).not.toContain('at ');
  });

  it('非 snapshot 端点 → CAPABILITY_UNAVAILABLE（后续 stage）', async () => {
    const handler = createHandler(snapshotService);
    const result = (await handler(
      'run/detail',
      { runId: '20261028-120000-abcdef' },
      new AbortController().signal,
      {},
    )) as { ok: boolean; error?: { message: string } };
    expect(result.ok).toBe(false);
    expect(result.error?.message).toContain('后续 stage');
  });

  it('未知端点 → 未知端点错误', async () => {
    const handler = createHandler(snapshotService);
    const result = (await handler('nope', {}, new AbortController().signal, {})) as {
      ok: boolean;
      error?: { message: string };
    };
    expect(result.ok).toBe(false);
    expect(result.error?.message).toContain('未知端点');
  });
});
