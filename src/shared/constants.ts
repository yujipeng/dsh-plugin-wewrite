/**
 * 共享常量：RPC 通道、契约版本、错误码（设计 §4）。
 * 双端（host/client）共用的唯一契约载体，禁漂移。
 */

/** 平台 RPC 目标串带前导斜杠；客户端与 host 同通道拼接 endpoint。 */
export const RPC_CHANNEL = '/wewrite-python';

/** 契约版本：capabilities 协商与快照诊断使用。 */
export const CONTRACT_VERSION = 1;

/**
 * 业务错误码（设计 §4 固定 18 项，实际列出 17 项；以设计原文为准）。
 * 业务错误码只出现在成功传输 value 内的业务失败联合里，不写进宿主传输 error.code。
 */
export const ERROR_CODES = [
  'INVALID_INPUT',
  'NOT_FOUND',
  'FORBIDDEN',
  'VERSION_CONFLICT',
  'RUN_BUSY',
  'INVALID_TRANSITION',
  'CAPABILITY_UNAVAILABLE',
  'DEPENDENCY_MISMATCH',
  'ARTIFACT_MISSING',
  'REVIEW_REQUIRED',
  'APPROVAL_REQUIRED',
  'APPROVAL_DENIED',
  'APPROVAL_UNAVAILABLE',
  'TIMEOUT',
  'CANCELLED',
  'PUBLISH_UNKNOWN',
  'INTERNAL',
] as const;

export type ErrorCode = (typeof ERROR_CODES)[number];

/** 业务错误的中文文案表：错误信息为中文、不含栈与秘密。 */
export const ERROR_MESSAGES: Readonly<Record<ErrorCode, string>> = {
  INVALID_INPUT: '请求参数不合法',
  NOT_FOUND: '目标不存在',
  FORBIDDEN: '无权限执行该操作',
  VERSION_CONFLICT: '内容版本冲突，请刷新后重试',
  RUN_BUSY: '该写作任务正被占用',
  INVALID_TRANSITION: '当前状态不允许此操作',
  CAPABILITY_UNAVAILABLE: '所需能力不可用',
  DEPENDENCY_MISMATCH: '依赖版本不匹配',
  ARTIFACT_MISSING: '产物缺失',
  REVIEW_REQUIRED: '需要先完成审稿',
  APPROVAL_REQUIRED: '需要审批',
  APPROVAL_DENIED: '审批被拒绝',
  APPROVAL_UNAVAILABLE: '审批服务不可用',
  TIMEOUT: '操作超时',
  CANCELLED: '操作已取消',
  PUBLISH_UNKNOWN: '推送结果未知',
  INTERNAL: '内部错误',
};
