/**
 * rpc.ts（设计 §4 范围第 1 条）：RPC Adapter —— payload 校验 + 转发 WorkbenchService + 响应形状复核。
 *
 * 信封契约：{ok:true,value:<业务结果>} / {ok:false,error:{code,message,details}}。
 * 业务错误只放在成功传输 value 内的业务失败联合；业务错误码不写进传输 error.code。
 * 身份校验（owner 浏览器/所属工作区）在 web-route.ts 层完成。
 */

import { rpcContract } from '../shared/contract';
import { toHostFailure } from './envelope';
import type { RpcHandler } from './types';
import { redactError } from './redaction';
import type { WorkbenchService } from './workbench-service';

interface SchemaLike {
  safeParse(input: unknown): { success: boolean; data?: unknown; error?: { issues: { path: (string | number)[]; message: string }[] } };
}

type ContractEntry = { request: SchemaLike; response: SchemaLike };

const CONTRACT: Record<string, ContractEntry> = rpcContract as unknown as Record<string, ContractEntry>;

function issueText(issue: { path: (string | number)[]; message: string }): string {
  const path = issue.path.length > 0 ? `${issue.path.join('.')}: ` : '';
  return `${path}${issue.message}`;
}

type EndpointHandler = (service: WorkbenchService, payload: Record<string, unknown>) => Promise<unknown>;

const HANDLERS: Record<string, EndpointHandler> = {
  snapshot: (s) => s.snapshot(),
  'run/list': (s, p) => s.runList(p as never),
  'hotspots/fetch': (s, p) => s.hotspotsFetch(p as never),
  'run/create': (s, p) => s.runCreate(p as never),
  'run/start': (s, p) => s.runStart(p as never),
  'run/resume': (s, p) => s.runResume(p as never),
  'run/detail': (s, p) => s.runDetail(p as never),
  'run/cancel': (s, p) => s.runCancel(p as never),
  'article/get': (s, p) => s.articleGet(p as never),
  'article/save': (s, p) => s.articleSave(p as never),
  'article/fork': (s, p) => s.articleFork(p as never),
  'article/review': (s, p) => s.articleReview(p as never),
  'article/preview': (s, p) => s.articlePreview(p as never),
  'asset/put': (s, p) => s.assetPut(p as never),
  'asset/get': (s, p) => s.assetGet(p as never),
  'wechat/prepareDraft': (s, p) => s.prepareDraft(p as never),
  'wechat/pushDraft': (s, p) => s.pushDraft(p as never),
  'wechat/pushStatus': (s, p) => s.pushStatus(p as never),
};

export function createHandler(service: WorkbenchService): RpcHandler {
  return async (endpoint, payload) => {
    const entry = CONTRACT[endpoint];
    if (!entry) {
      return toHostFailure(new Error(`未知端点：${endpoint}`));
    }
    const parsed = entry.request.safeParse(payload ?? {});
    if (!parsed.success || parsed.data === undefined) {
      const issues = parsed.error?.issues ?? [];
      return toHostFailure(new Error(`请求校验失败（${endpoint}）：${issues.map(issueText).join('; ')}`));
    }
    const handler = HANDLERS[endpoint];
    if (!handler) {
      return toHostFailure(new Error(`端点 ${endpoint} 未实现`));
    }
    try {
      const result = await handler(service, parsed.data as Record<string, unknown>);
      const checked = entry.response.safeParse(result);
      if (!checked.success || checked.data === undefined) {
        const issues = checked.error?.issues ?? [];
        return toHostFailure(new Error(`响应形状漂移（${endpoint}）：${issues.map(issueText).join('; ')}`));
      }
      return { ok: true as const, value: checked.data };
    } catch (error) {
      return toHostFailure(error);
    }
  };
}

/** 业务失败日志脱敏（无栈、无秘密）。 */
export function redactForLog(error: unknown): string {
  return redactError(error);
}
