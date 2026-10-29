/**
 * push-approval.ts（设计 §7 双闸第一闸 + §8 执行令牌证据）。
 *
 * - 为 `wewrite_push_draft` 武装 `tools/pre-execute`：精确匹配该工具返回
 *   `{kind:'ask',reason:...}`（含标题、账号、版本、封面、门禁、仅草稿），其他工具调用 next。
 * - 审批服务缺失/抛错/监听被回收时不注册推送工具（fail-closed）。
 * - 不允许「始终允许」替代逐次 allowed-once：本模块只产出 `ask`，从不产出 `allow`。
 * - 执行令牌不跨 exec 复用证据：`exec.token` 每次宿主重铸新 Symbol；本模块只读该 token
 *   用于进程内审计（两次推送 token 不相等 + 第二次独立 asked），不缓存/复用 allowed-once。
 */

import type { PreToolDecision, ToolExecution } from '@deepseek-ai/dsh-tools';
import type { ApprovalService, ApprovalOutcome } from '@deepseek-ai/dsh-user-approval';

export const PUSH_TOOL_NAME = 'wewrite_push_draft';

/** 审批服务最小消费面（对标本机 DSH 0.2.1-rc.4）。 */
export interface ApprovalRuntime {
  request(req: {
    readonly agent: unknown;
    readonly toolName: string;
    readonly callId?: unknown;
    readonly reason?: string;
    readonly displayReason?: { readonly en: string; readonly [locale: string]: string };
    readonly signal?: AbortSignal;
  }): Promise<ApprovalOutcome>;
}

/** 组装 ask 理由：标题、账号、版本、封面、门禁、仅草稿（设计 §7 第一闸）。 */
export function pushAskReason(summary: {
  readonly title?: string;
  readonly accountLabel?: string;
  readonly articleVersion?: string;
  readonly coverHash?: string;
  readonly gate?: string;
}): string {
  const parts = [
    `推送微信草稿（仅草稿，不群发）`,
    `标题：${summary.title ?? '（未命名）'}`,
    `账号：${summary.accountLabel ?? '未配置'}`,
    `正文版本：${summary.articleVersion ?? '未知'}`,
    `封面：${summary.coverHash ?? '无'}`,
    `门禁：${summary.gate ?? '未审稿'}`,
  ];
  return parts.join('；');
}

/**
 * 探测审批服务是否真实可用（存在 request + 有 Web 交互）。
 * 只做存在性探测，不真正弹 ask——真弹 ask 由宿主正常调度在 open turn 内执行。
 */
export function probeApproval(approval: unknown): ApprovalRuntime | undefined {
  if (!approval || typeof approval !== 'object') return undefined;
  const candidate = approval as { request?: unknown };
  if (typeof candidate.request !== 'function') return undefined;
  return approval as ApprovalRuntime;
}

/**
 * 为推送工具武装 `tools/pre-execute` 第一闸（waterfall hook）。
 *
 * - 精确匹配 `wewrite_push_draft` 返回 ask；其他工具 next()。
 * - 永不返回 allow（不允许「始终允许」替代逐次 allowed-once）。
 * - 返回监听 disposer（由宿主登记，监听回收时不再注册推送工具）。
 *
 * `ctx` 是 agent 的 scoped Cordis Context：`tools/pre-execute` 是 Context 事件，
 * 经 `ctx.on('tools/pre-execute', listener)` 注册，不是 ToolRuntime 的方法。
 */
export function installPushPreExecute(
  ctx: {
    on?(name: string, listener: (exec: ToolExecution, next: () => Promise<PreToolDecision>) => Promise<PreToolDecision>): () => void;
  },
  reasonFor: (exec: ToolExecution) => string,
): () => void {
  const listener = async (exec: ToolExecution, next: () => Promise<PreToolDecision>): Promise<PreToolDecision> => {
    if (exec.name !== PUSH_TOOL_NAME) return next();
    return { kind: 'ask', reason: reasonFor(exec) };
  };
  if (typeof ctx.on === 'function') {
    return ctx.on('tools/pre-execute', listener) ?? (() => undefined);
  }
  return () => undefined;
}

/** 审批服务引用（进程内单例，供装配与测试替换）。 */
let approvalRef: ApprovalRuntime | undefined;

export function setApprovalRuntime(approval: ApprovalRuntime | undefined): void {
  approvalRef = approval;
}

export function getApprovalRuntime(): ApprovalRuntime | undefined {
  return approvalRef;
}

/** intentId → 推送摘要（供第一闸 ask 理由构造；prepareDraft 成功后写入，intent 终态后清）。 */
const pushSummaryRegistry = new Map<string, PushSummaryLike>();

export interface PushSummaryLike {
  readonly title?: string;
  readonly accountLabel?: string;
  readonly articleVersion?: string;
  readonly coverHash?: string;
  readonly gate?: string;
}

export function registerPushSummary(intentId: string, summary: PushSummaryLike): void {
  pushSummaryRegistry.set(intentId, summary);
}

export function pushSummaryOf(intentId: string): PushSummaryLike | undefined {
  return pushSummaryRegistry.get(intentId);
}

export function clearPushSummary(intentId: string): void {
  pushSummaryRegistry.delete(intentId);
}

/** 从工具入参提取 intentId（第一闸 reason 构造用）。 */
export function intentIdFromArgs(args: unknown): string | undefined {
  if (args && typeof args === 'object') {
    const value = (args as { intentId?: unknown }).intentId;
    return typeof value === 'string' ? value : undefined;
  }
  return undefined;
}

export type { ApprovalService };
