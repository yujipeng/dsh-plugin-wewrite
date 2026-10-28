/**
 * RPC 适配层（设计 §4）：只做 payload 校验 + 转发 + 响应形状复核。
 * 信封契约：{ok:true,value:<业务结果>} / {ok:false,error:{code,message,details}}。
 * 业务错误只放在成功传输 value 内的业务失败联合（此骨架仅 snapshot 成功路径，
 * 业务失败在后续 stage 的 service 层产生），业务错误码不写进传输 error.code。
 */

import { rpcContract } from '../shared/contract';
import { ERROR_MESSAGES, type ErrorCode } from '../shared/constants';
import { redactError, truncateMessage } from './redaction';
import { WeWriteError, type RpcHandler } from './types';

interface SchemaLike {
  safeParse(input: unknown): { success: boolean; data?: unknown; error?: { issues: { path: (string | number)[]; message: string }[] } };
}

type ContractEntry = { request: SchemaLike; response: SchemaLike };

const CONTRACT: Record<string, ContractEntry> = rpcContract as unknown as Record<string, ContractEntry>;

/** 骨架期业务服务最小面：只实现 snapshot 的最小诊断版。 */
export interface SkeletonService {
  snapshot(): Promise<unknown>;
}

function issueText(issue: { path: (string | number)[]; message: string }): string {
  const path = issue.path.length > 0 ? `${issue.path.join('.')}: ` : '';
  return `${path}${issue.message}`;
}

/** 宿主传输错误信封：code 固定 'internal'（宿主白名单最通用分支），真实信息只进脱敏 message。 */
function toHostFailure(error: unknown): { ok: false; error: { code: string; message: string; details: Record<string, never> } } {
  const message = truncateMessage(redactError(error));
  return { ok: false, error: { code: 'internal', message, details: {} } };
}

/** 业务失败联合（§4：成功传输 value 内）。 */
export function businessFailure(code: ErrorCode, message?: string, extra?: { retryable?: boolean; runId?: string }) {
  return {
    ok: false as const,
    error: {
      code,
      message: message ?? ERROR_MESSAGES[code],
      retryable: extra?.retryable ?? false,
      ...(extra?.runId !== undefined ? { runId: extra.runId } : {}),
    },
  };
}

export function createHandler(service: SkeletonService): RpcHandler {
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
    try {
      let result: unknown;
      if (endpoint === 'snapshot') {
        result = await service.snapshot();
      } else {
        return toHostFailure(
          new WeWriteError('CAPABILITY_UNAVAILABLE', `端点 ${endpoint} 属后续 stage，本骨架仅提供 snapshot 最小诊断版`),
        );
      }
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

