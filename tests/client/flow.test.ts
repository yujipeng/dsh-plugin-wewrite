/**
 * 「写这个」两跳流程测试（返修 2）：runCreate 成功后必须 runStart，失败按业务错误呈现。
 */

import { describe, expect, it } from 'vitest';
import { startWriting } from '@/client/index';
import { WeWriteClient } from '@/client/rpc';
import type { ClientConnection } from '@/client/context';

function fakeConnection(handler: (endpoint: string, payload: unknown) => { ok: boolean; value?: unknown; error?: { code: string; message: string; details: object } }) {
  const calls: Array<{ endpoint: string; payload: unknown }> = [];
  const connection: ClientConnection = {
    rpc: {
      call: async (_channel, endpoint, payload) => {
        calls.push({ endpoint, payload });
        return handler(endpoint, payload);
      },
    },
  };
  return { connection, calls };
}

describe('startWriting（返修 2：runCreate → runStart 两跳）', () => {
  it('两跳均成功：先 create 后 start，交付 runId', async () => {
    const { connection, calls } = fakeConnection((endpoint) => {
      if (endpoint === 'run/create') return { ok: true, value: { ok: true, data: { runId: '20261028-120000-abcdef', phase: 'created' } } };
      if (endpoint === 'run/start') return { ok: true, value: { ok: true, data: { binding: { runId: '20261028-120000-abcdef', agentId: 'a', sessionId: 's', workspaceId: 'w', workflowId: 'wf', generation: 1, phase: 'starting' } } } };
      return { ok: false, error: { code: 'INTERNAL', message: '未知端点', details: {} } };
    });
    const client = new WeWriteClient(connection);
    let n = 0;
    const result = await startWriting('主题', 't1', 'ws-1', client, () => `r${++n}`);
    expect(result.ok).toBe(true);
    expect(calls.map((c) => c.endpoint)).toEqual(['run/create', 'run/start']);
    expect((result as { runId: string }).runId).toBe('20261028-120000-abcdef');
  });

  it('create 失败：不发起 start，返回业务错误', async () => {
    const { connection, calls } = fakeConnection((endpoint) => {
      if (endpoint === 'run/create') return { ok: true, value: { ok: false, error: { code: 'INVALID_INPUT', message: '主题不合法', retryable: false } } };
      return { ok: false, error: { code: 'INTERNAL', message: '未知端点', details: {} } };
    });
    const client = new WeWriteClient(connection);
    const result = await startWriting('主题', 't1', 'ws-1', client, () => 'r');
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.message).toBe('主题不合法');
    expect(calls.map((c) => c.endpoint)).toEqual(['run/create']);
  });

  it('create 成功 + start 失败：不静默，返回业务错误', async () => {
    const { connection, calls } = fakeConnection((endpoint) => {
      if (endpoint === 'run/create') return { ok: true, value: { ok: true, data: { runId: '20261028-120000-abcdef', phase: 'created' } } };
      if (endpoint === 'run/start') return { ok: true, value: { ok: false, error: { code: 'RUN_BUSY', message: '该写作任务正被占用', retryable: false } } };
      return { ok: false, error: { code: 'INTERNAL', message: '未知端点', details: {} } };
    });
    const client = new WeWriteClient(connection);
    const result = await startWriting('主题', 't1', 'ws-1', client, () => 'r');
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.message).toBe('该写作任务正被占用');
    expect(calls.map((c) => c.endpoint)).toEqual(['run/create', 'run/start']);
  });

  it('缺 workspaceId：零 runCreate 调用（四轮-3：先定上下文再建档）', async () => {
    const { connection, calls } = fakeConnection((endpoint) => {
      if (endpoint === 'run/create') return { ok: true, value: { ok: true, data: { runId: '20261028-120000-abcdef', phase: 'created' } } };
      return { ok: false, error: { code: 'INTERNAL', message: '未知端点', details: {} } };
    });
    const client = new WeWriteClient(connection);
    const result = await startWriting('主题', 't1', null, client, () => 'r');
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.message).toContain('工作区');
    expect(calls).toEqual([]); // runCreate 零调用
  });
});
