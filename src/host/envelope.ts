/**
 * 业务信封（设计 §4）：成功传输 value 内的业务联合 {ok:true,data} / {ok:false,error}。
 * 与宿主传输信封 {ok:true,value} / {ok:false,error:{code,message,details}} 分层。
 * 本模块被 rpc.ts 与 workbench-service.ts 共用，避免循环依赖。
 */

import { ERROR_MESSAGES, type ErrorCode } from '../shared/constants';
import { redactError, truncateMessage } from './redaction';

export type BusinessResult<T = unknown> =
  | { ok: true; data: T }
  | { ok: false; error: BusinessFailure };

export interface BusinessFailure {
  code: ErrorCode;
  message: string;
  retryable: boolean;
  runId?: string;
}

export function businessFailure(code: ErrorCode, message?: string, extra?: { retryable?: boolean; runId?: string }): BusinessResult<never> {
  return {
    ok: false,
    error: {
      code,
      message: message ?? ERROR_MESSAGES[code],
      retryable: extra?.retryable ?? false,
      ...(extra?.runId !== undefined ? { runId: extra.runId } : {}),
    },
  };
}

export function businessSuccess<T>(data: T): BusinessResult<T> {
  return { ok: true, data };
}

export function isBusinessFailure(value: unknown): value is { ok: false; error: BusinessFailure } {
  return (
    typeof value === 'object' &&
    value !== null &&
    (value as { ok?: unknown }).ok === false &&
    typeof (value as { error?: unknown }).error === 'object'
  );
}

/** 宿主传输失败信封：code 固定 'internal'，真实信息只进脱敏 message。 */
export function toHostFailure(error: unknown): { ok: false; error: { code: string; message: string; details: Record<string, never> } } {
  const message = truncateMessage(redactError(error));
  return { ok: false, error: { code: 'internal', message, details: {} } };
}
