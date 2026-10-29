/**
 * client RPC 客户端（设计 §3/§4）：通过宿主 connection 服务发请求，双端契约校验。
 * - client 不接触文件系统/Python/微信密钥，只走 RPC。
 * - 使用 rc.4 客户端 `ClientConnectionRpc.call(channel, endpoint, payload, signal?)`（正式方法）。
 * - 四态（空/加载/错误/无权限）由面板处理，这里只返回业务联合或明确错误。
 */

import { rpcContract, RPC_CHANNEL } from '../shared/contract';
import type { ClientConnection } from './context';

export type ClientBusiness<T = unknown> =
  | { readonly ok: true; readonly data: T }
  | { readonly ok: false; readonly error: { readonly code: string; readonly message: string; readonly retryable: boolean; readonly runId?: string } };

/** 宿主演进传输结果：{ok:true,value:<业务联合>} / {ok:false,error:{code,message,details}}。 */
interface ConnectionResult {
  readonly ok: boolean;
  readonly value?: unknown;
  readonly error?: { readonly code: string; readonly message: string; readonly details: object };
}

function connectionFailure(message: string, code = 'CAPABILITY_UNAVAILABLE'): ClientBusiness<never> {
  return { ok: false, error: { code, message, retryable: false } };
}

export class WeWriteClient {
  constructor(private readonly connection: ClientConnection) {}

  private async call<T>(endpoint: string, payload: unknown): Promise<ClientBusiness<T>> {
    const entry = (rpcContract as unknown as Record<string, { response: { safeParse: (v: unknown) => { success: boolean; data?: unknown; error?: unknown } } }>)[endpoint];
    if (!entry) return connectionFailure('未知端点', 'INVALID_INPUT');
    if (typeof this.connection?.rpc?.call !== 'function') {
      return connectionFailure('RPC 通道不可用');
    }

    let result: ConnectionResult;
    try {
      result = await this.connection.rpc.call(RPC_CHANNEL, endpoint, payload);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      return { ok: false, error: { code: 'INTERNAL', message, retryable: false } };
    }

    if (!result || result.ok !== true) {
      const err = result?.error;
      return {
        ok: false,
        error: {
          code: 'INTERNAL',
          message: typeof err?.message === 'string' ? err.message : '内部错误',
          retryable: false,
        },
      };
    }

    // 传输成功：value 内是业务联合 {ok:true,data}|{ok:false,error}，做响应形状复核。
    const checked = entry.response.safeParse(result.value);
    if (!checked.success) {
      return connectionFailure('响应形状漂移', 'INTERNAL');
    }
    return checked.data as ClientBusiness<T>;
  }

  snapshot() { return this.call<unknown>('snapshot', {}); }
  runList(payload: { cursor?: string; limit?: number }) { return this.call<unknown>('run/list', payload); }
  hotspotsFetch(payload: { limit: number }) { return this.call<unknown>('hotspots/fetch', payload); }
  runCreate(payload: unknown) { return this.call<unknown>('run/create', payload); }
  runStart(payload: unknown) { return this.call<unknown>('run/start', payload); }
  runResume(payload: unknown) { return this.call<unknown>('run/resume', payload); }
  runDetail(payload: { runId: string }) { return this.call<unknown>('run/detail', payload); }
  runCancel(payload: unknown) { return this.call<unknown>('run/cancel', payload); }
  articleGet(payload: { runId: string; kind: string }) { return this.call<unknown>('article/get', payload); }
  articleSave(payload: unknown) { return this.call<unknown>('article/save', payload); }
  articleFork(payload: unknown) { return this.call<unknown>('article/fork', payload); }
  articleReview(payload: unknown) { return this.call<unknown>('article/review', payload); }
  articlePreview(payload: unknown) { return this.call<unknown>('article/preview', payload); }
  assetPut(payload: unknown) { return this.call<unknown>('asset/put', payload); }
  assetGet(payload: { runId: string; assetId: string }) { return this.call<unknown>('asset/get', payload); }
  prepareDraft(payload: unknown) { return this.call<unknown>('wechat/prepareDraft', payload); }
  pushDraft(payload: unknown) { return this.call<unknown>('wechat/pushDraft', payload); }
  pushStatus(payload: { runId: string; operationId: string }) { return this.call<unknown>('wechat/pushStatus', payload); }
}
