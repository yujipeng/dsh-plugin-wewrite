/**
 * client RPC 客户端（设计 §3/§4）：通过宿主 connection 服务发请求，双端契约校验。
 * - client 不接触文件系统/Python/微信密钥，只走 RPC。
 * - 四态（空/加载/错误/无权限）由面板处理，这里只返回业务联合或明确错误。
 */

import type { z } from 'zod';
import { rpcContract, RPC_CHANNEL } from '../shared/contract';

export interface ClientRpcOptions {
  /** 宿主 connection 客户端（dsh-client-connection 的 client 面）。 */
  readonly connection: {
    request?(channel: string, endpoint: string, payload: unknown): Promise<unknown>;
  };
}

export type ClientBusiness<T = unknown> =
  | { readonly ok: true; readonly data: T }
  | { readonly ok: false; readonly error: { readonly code: string; readonly message: string; readonly retryable: boolean; readonly runId?: string } };

export class WeWriteClient {
  constructor(private readonly options: ClientRpcOptions) {}

  private async call<T>(endpoint: string, payload: unknown): Promise<ClientBusiness<T>> {
    const entry = (rpcContract as unknown as Record<string, { response: { safeParse: (v: unknown) => { success: boolean; data?: unknown; error?: unknown } } }>)[endpoint];
    if (!entry) return { ok: false, error: { code: 'INVALID_INPUT', message: '未知端点', retryable: false } };
    if (typeof this.options.connection.request !== 'function') {
      return { ok: false, error: { code: 'CAPABILITY_UNAVAILABLE', message: 'RPC 通道不可用', retryable: false } };
    }
    const raw = await this.options.connection.request(RPC_CHANNEL, endpoint, payload);
    const envelope = raw as { result?: { ok?: boolean; value?: unknown; error?: unknown } } | undefined;
    const result = envelope?.result;
    if (!result || result.ok !== true) {
      const err = (result?.error as { message?: string } | undefined) ?? {};
      return { ok: false, error: { code: 'INTERNAL', message: String(err.message ?? '内部错误'), retryable: false } };
    }
    const checked = entry.response.safeParse(result.value);
    if (!checked.success) {
      return { ok: false, error: { code: 'INTERNAL', message: '响应形状漂移', retryable: false } };
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
