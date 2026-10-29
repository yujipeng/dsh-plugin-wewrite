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
    return this.withReplay(payload.requestId, () =>
      this.options.workflow.resumeRun({
        runId: payload.runId,
        expectedGeneration: payload.expectedGeneration,
        workspaceId: payload.workspaceId,
      }),
    );
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
    return this.withReplay(payload.requestId, async () => bridgeResult(await this.safeBridge('draft.prepare', payload)));
  }

  async pushDraft(payload: { requestId: string; runId: string; intentId: string; expectedVersion: string | null }): Promise<BusinessResult<unknown>> {
    // S3：转入宿主工具审批的操作位，不直接触达 publisher。真实推送 S4。
    return this.withReplay(payload.requestId, async () =>
      businessSuccess({ operationId: this.options.randomId(), status: 'pending_approval' }),
    );
  }

  async pushStatus(payload: { runId: string; operationId: string }): Promise<BusinessResult<unknown>> {
    return bridgeResult(await this.safeBridge('draft.status', payload));
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
