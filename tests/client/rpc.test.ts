/**
 * 客户端 RPC 客户端测试（设计 §4 双端契约）：响应形状复核、错误中文。
 */

import { describe, expect, it } from 'vitest';
import { WeWriteClient } from '@/client/rpc';

function connectionWith(result: unknown) {
  return {
    request: async (_channel: string, _endpoint: string, _payload: unknown) => ({
      result,
    }),
  };
}

describe('WeWriteClient（双端契约）', () => {
  it('snapshot 成功联合被解析为 {ok:true,data}', async () => {
    const data = {
      runs: [],
      nextCursor: null,
      themes: [],
      capabilities: { read: true, write: false, agent: false, skills: false, preview: false, push: false },
      diagnostics: [],
      settings: { visualMode: 'none', pushEnabled: false, publishTransport: 'fake', credentialConfigured: false },
    };
    const client = new WeWriteClient({ connection: connectionWith({ ok: true, value: { ok: true, data } }) });
    const result = await client.snapshot();
    expect(result.ok).toBe(true);
    if (result.ok) expect(result.data).toBeDefined();
  });

  it('响应形状漂移 → {ok:false,error}', async () => {
    const client = new WeWriteClient({ connection: connectionWith({ ok: true, value: { ok: true, data: { __bogus: 1 } } }) });
    const result = await client.snapshot();
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.error.message).toBe('响应形状漂移');
  });

  it('RPC 通道不可用 → CAPABILITY_UNAVAILABLE', async () => {
    const client = new WeWriteClient({ connection: {} });
    const result = await client.snapshot();
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.error.code).toBe('CAPABILITY_UNAVAILABLE');
  });
});
