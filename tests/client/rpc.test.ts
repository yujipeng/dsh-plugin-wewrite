/**
 * 客户端 RPC 客户端测试（设计 §4 双端契约）：宿主 connection.rpc.call 接线、响应形状复核、错误中文。
 */

import { describe, expect, it } from 'vitest';
import { WeWriteClient } from '@/client/rpc';
import type { ClientConnection } from '@/client/context';

function connectionWith(result: unknown): ClientConnection {
  return {
    rpc: {
      call: async (_channel: string, _endpoint: string, _payload: unknown) =>
        result as { ok: boolean; value?: unknown; error?: { code: string; message: string; details: object } },
    },
  };
}

describe('WeWriteClient（宿主 connection.rpc.call 接线）', () => {
  it('snapshot 成功联合被解析为 {ok:true,data}，并确实调用 rpc.call', async () => {
    const data = {
      runs: [],
      nextCursor: null,
      themes: [],
      capabilities: { read: true, write: false, agent: false, skills: false, preview: false, push: false },
      diagnostics: [],
      settings: { visualMode: 'none', pushEnabled: false, publishTransport: 'fake', credentialConfigured: false },
    };
    let called = false;
    const connection: ClientConnection = {
      rpc: {
        call: async (channel, endpoint, payload) => {
          called = true;
          expect(channel).toBe('/wewrite-python');
          expect(endpoint).toBe('snapshot');
          return { ok: true, value: { ok: true, data } };
        },
      },
    };
    const client = new WeWriteClient(connection);
    const result = await client.snapshot();
    expect(called).toBe(true);
    expect(result.ok).toBe(true);
    if (result.ok) expect(result.data).toBeDefined();
  });

  it('传输失败（ok:false）→ 业务 INTERNAL，中文消息', async () => {
    const connection = connectionWith({ ok: false, error: { code: 'internal', message: '请求校验失败', details: {} } });
    const client = new WeWriteClient(connection);
    const result = await client.snapshot();
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.error.code).toBe('INTERNAL');
  });

  it('响应形状漂移 → {ok:false,error}', async () => {
    const client = new WeWriteClient(connectionWith({ ok: true, value: { ok: true, data: { __bogus: 1 } } }));
    const result = await client.snapshot();
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.error.message).toBe('响应形状漂移');
  });

  it('RPC 通道不可用（缺 rpc.call）→ CAPABILITY_UNAVAILABLE', async () => {
    const client = new WeWriteClient({ rpc: { call: undefined as unknown as ClientConnection['rpc']['call'] } });
    const result = await client.snapshot();
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.error.code).toBe('CAPABILITY_UNAVAILABLE');
  });
});
