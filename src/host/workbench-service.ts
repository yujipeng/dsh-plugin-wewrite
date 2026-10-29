/**
 * workbench-service.ts（设计 §2/§4/§5 范围第 2 条）：统一业务 Module。
 *
 * 身份/任务绑定、Agent 生命周期、内容版本、审批、推送幂等、错误脱敏。
 * RPC 与 Agent 工具是同一 Interface 的两个 Adapter，状态逻辑集中在此，不散落各处。
 *
 * 业务失败放成功值内的 {ok:false,error}；传输层失败才用宿主 ConnectionRpcResult 的 ok:false。
 * 所有请求过严格 schema（rpc.ts 层），本 service 只消费已校验 payload 并返回业务联合。
 */

import type {
  Assessment,
  Binding,
  RunSummary,
  ToolBindingContext,
} from '../shared/contract';
import { businessFailure, businessSuccess, type BusinessResult } from './envelope';
import type { AgentWorkflow } from './agent-workflow';
import { redactError } from './redaction';
import { registerPushSummary } from './push-approval';
import { timeoutFor, type BridgeResult } from './wewrite-bridge';

/** facade 子进程调用面（注入以便测试替换为内存假实现）。 */
export type WorkbenchBridge = (operation: string, payload: Record<string, unknown>) => Promise<{ ok: boolean; data?: unknown; error?: unknown }>;

export interface WorkbenchServiceOptions {
  readonly bridge: WorkbenchBridge;
  readonly workflow: AgentWorkflow;
  readonly randomId: () => string;
  /** 请求级 requestId 重放缓存（同一身份与操作重放复用结果，不同参数报冲突）。 */
  readonly replay?: Map<string, unknown>;
  readonly onError?: (message: string) => void;
  /** S4：是否启用推送（pushEnabled=true 才注册推送工具）。 */
  readonly pushEnabled?: () => boolean;
  /** S4：推送工具是否已武装（审批服务就绪 + ask 监听 + guard 已装）。 */
  readonly pushArmed?: () => boolean;
  /** S4：经私有 stdin 解析真实微信凭据（仅通过双闸后调用）。 */
  readonly resolveCredentials?: () => Promise<Record<string, string> | undefined>;
  /** S4：把推送请求经 agent.followup 入队（真实 open turn 路径）。 */
  readonly enqueuePushFollowup?: (payload: {
    runId: string;
    intentId: string;
    expectedVersion: string | null;
  }) => Promise<{ ok: boolean; error?: { code: string; message: string } }>;
}

type BridgeFailure = { code: string; message: string; retryable?: boolean; runId?: string };

function asFailure(resp: { ok: boolean; data?: unknown; error?: unknown }): BridgeFailure | null {
  if (resp.ok) return null;
  const err = resp.error as Partial<BridgeFailure> | undefined;
  if (!err || typeof err !== 'object') return { code: 'INTERNAL', message: '内部错误', retryable: false };
  return {
    code: String(err.code ?? 'INTERNAL'),
    message: String(err.message ?? '内部错误'),
    retryable: err.retryable === true,
    ...(typeof err.runId === 'string' ? { runId: err.runId } : {}),
  };
}

/** 把 facade 业务联合转成宿主业务联合，失败统一中文脱敏。 */
function bridgeResult<T>(resp: { ok: boolean; data?: unknown; error?: unknown }): BusinessResult<T> {
  if (resp.ok) return businessSuccess(resp.data as T);
  const failure = asFailure(resp);
  const code = (failure?.code ?? 'INTERNAL') as never;
  return businessFailure(code, failure?.message, {
    retryable: failure?.retryable ?? false,
    ...(failure?.runId ? { runId: failure.runId } : {}),
  }) as BusinessResult<T>;
}

export class WorkbenchService {
  constructor(private readonly options: WorkbenchServiceOptions) {}

  // ── 基础读面 ─────────────────────────────────────────────────────────────

  async snapshot(): Promise<BusinessResult<unknown>> {
    const resp = await this.safeBridge('snapshot', {});
    if (resp.ok === false) return businessFailure('INTERNAL', '读取工作台快照失败');
    return bridgeResult(resp);
  }

  async runList(payload: { cursor?: string; limit?: number }): Promise<BusinessResult<unknown>> {
    return bridgeResult(await this.safeBridge('run.list', { ...payload }));
  }

  async hotspotsFetch(payload: { limit: number }): Promise<BusinessResult<unknown>> {
    return bridgeResult(await this.safeBridge('hotspots.fetch', payload));
  }

  // ── run 生命周期 ────────────────────────────────────────────────────────

  async runCreate(payload: { requestId: string; topic: string; mode: string; visualMode: string; themeId: string }): Promise<BusinessResult<unknown>> {
    return this.withReplay(payload.requestId, async () => bridgeResult(await this.safeBridge('run.create', payload)));
  }

  async runStart(payload: { requestId: string; runId: string; workspaceId: string }): Promise<BusinessResult<unknown>> {
    return this.withReplay(payload.requestId, async () => {
      const detail = await this.runDetail({ runId: payload.runId });
      if (detail.ok === false) return detail;
      const run = (detail.data as { run: RunSummary }).run;
      return this.options.workflow.startRun({
        runId: payload.runId,
        workspaceId: payload.workspaceId,
        topic: run.title,
        mode: run.mode,
        visualMode: 'none',
        themeId: '',
      });
    });
  }

  async runResume(payload: { requestId: string; runId: string; expectedGeneration: number; workspaceId: string }): Promise<BusinessResult<unknown>> {
    return this.withReplay(payload.requestId, async () => {
      const result = await this.options.workflow.resumeRun({
        runId: payload.runId,
        expectedGeneration: payload.expectedGeneration,
        workspaceId: payload.workspaceId,
      });
      // run/resume 契约响应只有 {binding}；resumedExisting 是 host 内部观测值，不进 RPC。
      if (result.ok) {
        return businessSuccess({ binding: (result.data as { binding: unknown }).binding });
      }
      return result;
    });
  }

  async runDetail(payload: { runId: string }): Promise<BusinessResult<unknown>> {
    const resp = await this.safeBridge('run.detail', payload);
    const result = bridgeResult<{ binding: Binding | null }>(resp);
    if (result.ok) {
      // binding 由宿主注册表投影（facade 持久化 binding 为权威，这里用内存绑定覆盖）。
      const binding = this.options.workflow.bindingOf(payload.runId) ?? result.data.binding;
      result.data = { ...result.data, binding } as typeof result.data;
    }
    return result;
  }

  async runCancel(payload: { requestId: string; runId: string; generation: number }): Promise<BusinessResult<unknown>> {
    return this.withReplay(payload.requestId, () => this.options.workflow.cancelRun(payload));
  }

  // ── 内容与派生 ───────────────────────────────────────────────────────────

  async articleGet(payload: { runId: string; kind: string }): Promise<BusinessResult<unknown>> {
    return bridgeResult(await this.safeBridge('artifact.get', payload));
  }

  async articleSave(payload: { requestId: string; runId: string; markdown: string; expectedVersion: string | null; metadata: unknown }): Promise<BusinessResult<unknown>> {
    return this.withReplay(payload.requestId, async () => bridgeResult(await this.safeBridge('article.save', payload)));
  }

  async articleFork(payload: { requestId: string; runId: string; expectedVersion: string | null }): Promise<BusinessResult<unknown>> {
    return this.withReplay(payload.requestId, async () => bridgeResult(await this.safeBridge('run.fork', payload)));
  }

  async articleReview(payload: { requestId: string; runId: string; expectedVersion: string | null; workspaceId: string }): Promise<BusinessResult<unknown>> {
    // 审稿走真实 Agent turn：reuse 当前绑定（completed run 由宿主在编辑派生后进入）。
    const binding = this.options.workflow.bindingOf(payload.runId);
    if (!binding) {
      return businessFailure('RUN_BUSY', '该写作任务没有活跃会话');
    }
    return businessSuccess({ binding });
  }

  async articlePreview(payload: { requestId: string; runId: string; kind: string; expectedVersion: string | null; themeId: string }): Promise<BusinessResult<unknown>> {
    return this.withReplay(payload.requestId, async () => bridgeResult(await this.safeBridge('preview.render', payload)));
  }

  // ── 资产 ────────────────────────────────────────────────────────────────

  async assetPut(payload: { requestId: string; runId: string; role: string; mediaType: string; base64: string; expectedArticleVersion: string | null }): Promise<BusinessResult<unknown>> {
    return this.withReplay(payload.requestId, async () => bridgeResult(await this.safeBridge('asset.put', payload)));
  }

  async assetGet(payload: { runId: string; assetId: string }): Promise<BusinessResult<unknown>> {
    return bridgeResult(await this.safeBridge('asset.get', payload));
  }

  // ── 微信草稿（S3 只到预检 + 发请求，不真发）──────────────────────────────

  async prepareDraft(payload: { requestId: string; runId: string; expectedVersion: string | null; coverAssetId: string; themeId: string }): Promise<BusinessResult<unknown>> {
    const result = await this.withReplay(payload.requestId, async () => bridgeResult(await this.safeBridge('draft.prepare', payload)));
    // S4：prepare 成功后把摘要登记到第一闸 ask 理由（标题/账号/版本/封面/门禁）。
    if (result.ok) {
      const data = result.data as {
        intentId?: string;
        summary?: { title?: string; accountLabel?: string; articleVersion?: string; coverHash?: string; dryRun?: boolean };
        gate?: { publishable?: boolean };
      };
      if (data.intentId && data.summary) {
        registerPushSummary(data.intentId, {
          title: data.summary.title,
          accountLabel: data.summary.accountLabel,
          articleVersion: data.summary.articleVersion,
          coverHash: data.summary.coverHash,
          gate: data.gate?.publishable === true ? '审稿通过' : '未审稿',
        });
      }
    }
    return result;
  }

  async pushDraft(payload: { requestId: string; runId: string; intentId: string; expectedVersion: string | null }): Promise<BusinessResult<unknown>> {
    // S4：页面推送必须进入真实 open turn——把固定 runId/intentId/expectedVersion 经
    // agent.followup(UserMessage) 入队，Agent 在该真实 turn 内调用 wewrite_push_draft。
    // 不得自行 append 假 turn/start 绕过生命周期；不得把业务执行塞进审批回调。
    if (!this.options.pushEnabled?.()) {
      return businessFailure('CAPABILITY_UNAVAILABLE', '推送未启用');
    }
    return this.withReplay(payload.requestId, async () => {
      // 正文工作流占用中返回 RUN_BUSY（同一 run 已有活跃 Agent 时才走 followup）。
      if (!this.options.enqueuePushFollowup) {
        return businessFailure('CAPABILITY_UNAVAILABLE', '推送审批通道不可用');
      }
      const queued = await this.options.enqueuePushFollowup(payload);
      if (queued.ok === false) {
        const err = queued.error as { code?: string; message?: string };
        return businessFailure((err?.code as never) ?? 'INTERNAL', err?.message ?? '推送入队失败');
      }
      // operationId 用 intentId（intent 是推送唯一权威键），pushStatus 可据此追踪。
      return businessSuccess({ operationId: payload.intentId, status: 'pending_approval' });
    });
  }

  async pushStatus(payload: { runId: string; operationId: string }): Promise<BusinessResult<unknown>> {
    return bridgeResult(await this.safeBridge('draft.status', payload));
  }

  /**
   * S4 推送工具第二闸：execute 内业务状态复验 + 经私有 stdin 传凭据发 draft.send。
   * 这是业务状态复验，不是第二次证明宿主审批（宿主 ask 的 allowed-once 是第一闸权威）。
   */
  async toolPushDraft(args: { runId: string; intentId: string; expectedVersion: string | null }, ctx: ToolBindingContext): Promise<BusinessResult<unknown>> {
    this.assertToolContext(args.runId, ctx);
    // 第二闸：armed、pushEnabled、可信绑定、当前 generation（ctx 已从真实绑定取）。
    if (!this.options.pushEnabled?.()) {
      return businessFailure('CAPABILITY_UNAVAILABLE', '推送未启用');
    }
    if (!this.options.pushArmed?.()) {
      return businessFailure('APPROVAL_UNAVAILABLE', '推送审批通道未武装');
    }
    // 凭据只在通过双闸后取出（经私有 stdin 传给 facade；不写会话/日志/argv）。
    const credentials = this.options.resolveCredentials ? await this.options.resolveCredentials() : undefined;
    const transport = 'fake';
    return bridgeResult(
      await this.safeBridge('draft.send', {
        requestId: this.options.randomId(),
        runId: args.runId,
        intentId: args.intentId,
        expectedVersion: args.expectedVersion,
        transport,
        ...(credentials ? { credentials } : {}),
        context: { runId: ctx.runId, workflowId: ctx.workflowId, generation: ctx.generation },
      }),
    );
  }

  /** 私有恢复（设计修订版二 §5）：host 生命周期调用，不进浏览器 RPC / 模型工具。 */
  async recoverDraft(payload: { requestId: string; runId: string; intentId?: string }): Promise<BusinessResult<unknown>> {
    return bridgeResult(await this.safeBridge('draft.recover', payload));
  }

  // ── Agent 工具 Adapter（设计 §5 表格）───────────────────────────────────

  async toolRunStart(args: { runId: string; workflowId: string; generation: number }, ctx: ToolBindingContext): Promise<BusinessResult<unknown>> {
    // 只绑定已有 run 并读初态；不能在已绑定 scope 新建第二篇。
    this.assertToolContext(args.runId, ctx);
    const binding = this.options.workflow.bindingOf(args.runId);
    if (!binding) return businessFailure('FORBIDDEN', 'run 未绑定写作会话');
    if (binding.workflowId !== args.workflowId || binding.generation !== args.generation) {
      return businessFailure('CANCELLED', '写租约已失效，需重新接管', { runId: args.runId });
    }
    return businessSuccess({ binding });
  }

  async toolRunDetail(args: { runId: string }, ctx: ToolBindingContext): Promise<BusinessResult<unknown>> {
    this.assertToolContext(args.runId, ctx);
    return this.runDetail({ runId: args.runId });
  }

  async toolArtifactGet(args: { runId: string; kind: string }, ctx: ToolBindingContext): Promise<BusinessResult<unknown>> {
    this.assertToolContext(args.runId, ctx);
    return this.articleGet({ runId: args.runId, kind: args.kind });
  }

  async toolRunStep(args: { requestId: string; runId: string; step: string; status: string; error?: string }, ctx: ToolBindingContext): Promise<BusinessResult<unknown>> {
    this.assertToolContext(args.runId, ctx);
    return bridgeResult(
      await this.safeBridge('run.step', { ...args, context: { runId: ctx.runId, workflowId: ctx.workflowId, generation: ctx.generation } }),
    );
  }

  async toolArtifactWrite(args: { requestId: string; runId: string; kind: string; content: string; expectedVersion: string | null }, ctx: ToolBindingContext): Promise<BusinessResult<unknown>> {
    this.assertToolContext(args.runId, ctx);
    return bridgeResult(
      await this.safeBridge('artifact.write', { ...args, context: { runId: ctx.runId, workflowId: ctx.workflowId, generation: ctx.generation } }),
    );
  }

  async toolReview(args: { requestId: string; runId: string; articleVersion: string | null; draftVersion: string | null; assessment: Assessment }, ctx: ToolBindingContext): Promise<BusinessResult<unknown>> {
    this.assertToolContext(args.runId, ctx);
    return bridgeResult(
      await this.safeBridge('review.build', { ...args, context: { runId: ctx.runId, workflowId: ctx.workflowId, generation: ctx.generation } }),
    );
  }

  async toolRunFinish(args: { requestId: string; runId: string; expectedVersion: string | null; reportVersion: string | null }, ctx: ToolBindingContext): Promise<BusinessResult<unknown>> {
    this.assertToolContext(args.runId, ctx);
    return bridgeResult(
      await this.safeBridge('run.finish', { ...args, context: { runId: ctx.runId, workflowId: ctx.workflowId, generation: ctx.generation } }),
    );
  }

  async toolPreview(args: { requestId: string; runId: string; kind: string; expectedVersion: string | null; themeId: string }, ctx: ToolBindingContext): Promise<BusinessResult<unknown>> {
    this.assertToolContext(args.runId, ctx);
    return this.articlePreview({ ...args, kind: args.kind });
  }

  /** 工具入场上下文校验：参数 runId 必须与真实 Agent 绑定 runId 相同（服务端绑定，不信模型自报）。 */
  private assertToolContext(runId: string, ctx: ToolBindingContext): void {
    if (ctx.runId !== runId) {
      throw new Error('跨 run 参数被拒');
    }
  }

  /** 从真实 agent.id 查绑定（Agent 工具入场用）。 */
  bindingForAgent(agentId: string): Binding | undefined {
    return this.options.workflow.bindingForAgent(agentId);
  }

  // ── 内部 ────────────────────────────────────────────────────────────────

  private async safeBridge(operation: string, payload: Record<string, unknown>): Promise<{ ok: boolean; data?: unknown; error?: unknown }> {
    try {
      return await this.options.bridge(operation, payload);
    } catch (error) {
      this.options.onError?.(redactError(error));
      return { ok: false, error: { code: 'INTERNAL', message: '内部错误', retryable: false } };
    }
  }

  private async withReplay(requestId: string, fn: () => Promise<BusinessResult<unknown>>): Promise<BusinessResult<unknown>> {
    const replay = this.options.replay;
    if (replay) {
      const cached = replay.get(requestId);
      if (cached !== undefined) return cached as BusinessResult<unknown>;
    }
    const result = await fn();
    replay?.set(requestId, result);
    return result;
  }
}

/** 供 client/测试复用：把宿主传输结果拆包成业务联合。 */
export function unwrapTransport(result: { ok: boolean; value?: unknown; error?: unknown }): { ok: boolean; data?: unknown; error?: unknown } {
  if (result.ok) {
    const value = result.value as { ok?: boolean; data?: unknown; error?: unknown } | undefined;
    if (value && value.ok === false) return { ok: false, error: value.error };
    return { ok: true, data: value?.data };
  }
  return { ok: false, error: result.error };
}

export type { BridgeResult };
export { timeoutFor };
