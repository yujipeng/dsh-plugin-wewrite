/**
 * tools/（设计 §5 范围第 4 条）：8 个受控 Agent 工具。
 *
 * - 工具仅装在专属写作 Agent 的 scope（setup 内注册）。
 * - 每次工具执行从真实 exec.agent 查绑定；generation 取服务端绑定，不信模型自报。
 * - `wewrite_push_draft` 本 stage 不注册（S4）。
 * - 工具成功结构 {ok:true,data}，失败 {ok:false,error}，与业务契约一致。
 */

import type { ToolDefinition } from '@deepseek-ai/dsh-tools';
import { defineTool } from '@deepseek-ai/dsh-tools';
import type { ToolRunContext } from '@deepseek-ai/dsh-tools';
import type { ToolBindingContext } from '../../shared/contract';
import type { WorkbenchService } from '../workbench-service';

/** 工具名 → schema 名称（供 service 路由）。 */
export const AGENT_TOOL_NAMES = [
  'wewrite_run_start',
  'wewrite_run_detail',
  'wewrite_artifact_get',
  'wewrite_run_step',
  'wewrite_artifact_write',
  'wewrite_review',
  'wewrite_run_finish',
  'wewrite_preview',
] as const;

export type AgentToolName = (typeof AGENT_TOOL_NAMES)[number];

/** 工具白名单：guard 只放行这些工具，其余（含 run_code/shell/通用写入/代码执行/委派）一律 deny。 */
export const CONTROLLED_TOOL_SET: ReadonlySet<string> = new Set(AGENT_TOOL_NAMES);

/** reserved `run_code`（PTC 传输）名称。 */
export const RUN_CODE_NAME = 'run_code';

/**
 * 继承层 restrict 的最小面（设计 §5 第 1 点）：专属写作工作流不需要任何继承层工具，
 * 因此 allow 为空清单——继承的 shell/通用写入/代码执行/委派等全局/祖先层工具全部不可见。
 * 不把 scope-local 名称、未知名称或 reserved `run_code` 传给 restrict。
 */
export const INHERITED_TOOL_ALLOWLIST: readonly string[] = [];

/**
 * scope 内单调 guard（设计 §5 第 2 点）：实际调用名称不在受控执行清单即 deny，
 * 显式拒绝 reserved `run_code`、shell、通用文件写入、代码执行和委派。
 * 自身层受控插件工具也受相同 run/generation 检查（由 WorkbenchService 在 execute 内复核）。
 */
export function controlledToolGuard(execution: Readonly<{ name?: string }>): string | undefined {
  const toolName = execution?.name ?? '';
  if (toolName === RUN_CODE_NAME) return 'run_code 在本写作工作流中被禁用';
  if (CONTROLLED_TOOL_SET.has(toolName)) return undefined;
  return `工具 ${toolName} 不在受控写作清单内`;
}

/** 装配期注入的 WorkbenchService（模块级引用，工具 execute 时读取）。 */
let serviceRef: WorkbenchService | undefined;

export function setToolService(service: WorkbenchService): void {
  serviceRef = service;
}

/** 宿主工具注册面的最小消费（对标本机 DSH 0.2.1-rc.4 dsh-tools）。 */
export interface AgentToolRegistry {
  register(definition: unknown): () => void;
  restrict?(filter: { allow?: readonly string[]; deny?: readonly string[] }): () => void;
  guard?(guard: (execution: Readonly<{ name?: string }>) => string | undefined): () => void;
}

/**
 * 专属写作 Agent 的 scope 装配（设计 §5 两条路径），生产装配与证据测试共用同一实现，
 * 避免「两套 scope 装配」漂移。
 *
 * 1) 继承层：restrict({allow: 空清单}) 只过滤继承的全局/祖先层工具。
 * 2) 自身层与 reserved：guard 显式拒绝 run_code/自身层危险工具，native 插件工具仍可执行。
 */
export function setupAgentScope(agentCtx: { get?(name: string): unknown }, toolDefs: ToolDefinition[], onWarn?: (message: string) => void): void {
  const agentTools = agentCtx.get?.('tools') as AgentToolRegistry | undefined;
  if (typeof agentTools?.register !== 'function') return;
  for (const def of toolDefs) {
    try {
      agentTools.register(def);
    } catch (error) {
      onWarn?.(`工具 ${def.name} scope 注册失败：${error instanceof Error ? error.message : String(error)}`);
    }
  }
  if (typeof agentTools.restrict === 'function') {
    try {
      agentTools.restrict({ allow: [...INHERITED_TOOL_ALLOWLIST] });
    } catch (error) {
      onWarn?.(`tools.restrict 继承层过滤失败：${error instanceof Error ? error.message : String(error)}`);
    }
  }
  if (typeof agentTools.guard === 'function') {
    try {
      agentTools.guard(controlledToolGuard);
    } catch (error) {
      onWarn?.(`tools.guard scope 注册失败：${error instanceof Error ? error.message : String(error)}`);
    }
  }
}

/** 文本渲染：工具结果统一为中文摘要文本块，原始数据在 value 内（lossless JSON）。 */
function textBlock(text: string): { type: 'text'; text: string } {
  return { type: 'text', text };
}

function summarizeError(error: unknown): string {
  if (error && typeof error === 'object') {
    const err = error as { code?: unknown; message?: unknown };
    return `${String(err.code ?? '内部错误')}：${String(err.message ?? '')}`;
  }
  return String(error ?? '工具执行失败');
}

/** 工具执行业务结果 → 文本块（成功/失败统一中文，无栈）。 */
function renderResult(value: unknown): { type: 'text'; text: string } {
  const v = value as { ok?: boolean; data?: unknown; error?: unknown };
  if (v && v.ok === false) {
    return textBlock(summarizeError(v.error));
  }
  const data = v?.data;
  if (data === undefined || data === null) {
    return textBlock('已完成');
  }
  return textBlock(JSON.stringify(data, null, 2));
}

/** 从执行上下文解析真实绑定上下文：agent 必填（工具只在 Agent scope 注册）。 */
export function toolContextOf(service: WorkbenchService, agentId: string, args: Record<string, unknown>): ToolBindingContext {
  // 每次工具执行从真实 exec.agent 查绑定：先按 agentId 找到绑定，再校验参数 runId 与绑定 runId 一致。
  const binding = service.bindingForAgent(agentId);
  if (!binding) {
    throw new Error('该写作会话无有效 run 绑定');
  }
  const argRunId = args.runId;
  if (typeof argRunId === 'string' && argRunId !== binding.runId) {
    throw new Error('跨 run 参数被拒');
  }
  // generation 取服务端绑定（内存投影），不信模型自报。
  return { runId: binding.runId, workflowId: binding.workflowId, generation: binding.generation };
}

function runIdParam() {
  return { type: 'string' as const, description: '写作任务 runId（服务端绑定校验）' };
}

function requestIdParam() {
  return { type: 'string' as const, description: '幂等 requestId（重放复用结果）' };
}

function kindParam() {
  return { type: 'string' as const, enum: ['brief', 'claims', 'draft', 'article', 'review_report', 'sources', 'illustrated_article', 'image_prompts', 'images_manifest'] };
}

function stepParam() {
  return { type: 'string' as const, enum: ['topic', 'write', 'review', 'visual', 'publish'] };
}

function stepStatusParam() {
  return { type: 'string' as const, enum: ['pending', 'in_progress', 'completed', 'failed', 'skipped'] };
}

function stringParam(description: string, required = true) {
  return required ? { type: 'string' as const, description, required: true } : { type: 'string' as const, description };
}

function nullableHashParam(description: string) {
  return { oneOf: [{ type: 'string' as const, description }, { type: 'null' as const }] };
}

/** 生成一个受控工具定义。execute 内从 exec.agent 查绑定并调用同一 WorkbenchService。 */
function tool(
  name: AgentToolName,
  description: string,
  parameters: Record<string, unknown>,
  run: (args: Record<string, unknown>, ctx: ToolBindingContext, exec: ToolRunContext) => Promise<unknown>,
): ToolDefinition {
  return defineTool({
    name,
    description,
    parameters: parameters as never,
    output: {
      schema: { type: 'json' as const },
      render: (_args, value) => [renderResult(value)],
    },
    isConcurrencySafe: () => false,
    async execute(args, exec) {
      // 每次工具执行从真实 exec.agent 查绑定。
      const service = serviceRef;
      if (!service) throw new Error('工作台服务不可用');
      const agentId = exec.agent?.id;
      if (!agentId) throw new Error('工具只能在专属写作会话中调用');
      const ctx = toolContextOf(service, agentId, args as Record<string, unknown>);
      return (await run(args as Record<string, unknown>, ctx, exec)) as never;
    },
  });
}

export function buildAgentTools(service: WorkbenchService): ToolDefinition[] {
  const defs: ToolDefinition[] = [];

  defs.push(
    tool('wewrite_run_start', '读取已绑定 run 的初态（run 已创建，不要执行 wewrite run start/resume）。', {
      runId: runIdParam(),
      workflowId: stringParam('当前 workflowId'),
      generation: { type: 'integer' as const, description: '服务端绑定 generation' },
    }, (args, ctx) => service.toolRunStart(args as never, ctx)),
  );

  defs.push(
    tool('wewrite_run_detail', '读取当前绑定 run 的详情（状态/产物/门禁）。', {
      runId: runIdParam(),
    }, (args, ctx) => service.toolRunDetail(args as never, ctx)),
  );

  defs.push(
    tool('wewrite_artifact_get', '读取当前绑定 run 的指定产物内容。', {
      runId: runIdParam(),
      kind: kindParam(),
    }, (args, ctx) => service.toolArtifactGet(args as never, ctx)),
  );

  defs.push(
    tool('wewrite_run_step', '推进当前绑定 run 的步骤状态（服务端校验顺序，不能绕过审稿/封存）。', {
      requestId: requestIdParam(),
      runId: runIdParam(),
      step: stepParam(),
      status: stepStatusParam(),
      error: stringParam('步骤错误信息（可选）', false),
    }, (args, ctx) => service.toolRunStep(args as never, ctx)),
  );

  defs.push(
    tool('wewrite_artifact_write', '写入当前绑定 run 的产物（版本 CAS，article 写入立即使旧审稿失效）。', {
      requestId: requestIdParam(),
      runId: runIdParam(),
      kind: kindParam(),
      content: stringParam('产物文本内容'),
      expectedVersion: nullableHashParam('期望版本哈希或 null'),
    }, (args, ctx) => service.toolArtifactWrite(args as never, ctx)),
  );

  defs.push(
    tool('wewrite_review', '提交审稿评估，生成绑定两版正文的审稿报告与门禁。', {
      requestId: requestIdParam(),
      runId: runIdParam(),
      articleVersion: nullableHashParam('正文版本哈希或 null'),
      draftVersion: nullableHashParam('初稿版本哈希或 null'),
      assessment: {
        type: 'object' as const,
        additionalProperties: true,
        description: '审稿评估（decision=pass/revise/needs_input，pass_number=1|2，dimensions 五维 accuracy/viewpoint/usefulness/voice/readability 各 1–5 整数，blockers/major_issues 字符串数组，notes 字符串）',
        properties: {
          decision: { type: 'string' as const, enum: ['pass', 'revise', 'needs_input'], required: true },
          pass_number: { type: 'integer' as const, enum: [1, 2], required: true },
          dimensions: {
            type: 'object' as const,
            additionalProperties: false,
            required: true,
            properties: {
              accuracy: { type: 'integer' as const, required: true },
              viewpoint: { type: 'integer' as const, required: true },
              usefulness: { type: 'integer' as const, required: true },
              voice: { type: 'integer' as const, required: true },
              readability: { type: 'integer' as const, required: true },
            },
          },
          blockers: { type: 'array' as const, items: { type: 'string' as const }, required: true },
          major_issues: { type: 'array' as const, items: { type: 'string' as const }, required: true },
          notes: { type: 'string' as const, required: true },
        },
      },
    }, (args, ctx) => service.toolReview(args as never, ctx)),
  );

  defs.push(
    tool('wewrite_run_finish', '验证真实文件/来源/报告后 finish_run（仅此操作进入 ready）。', {
      requestId: requestIdParam(),
      runId: runIdParam(),
      expectedVersion: nullableHashParam('正文版本哈希或 null'),
      reportVersion: nullableHashParam('报告版本哈希或 null'),
    }, (args, ctx) => service.toolRunFinish(args as never, ctx)),
  );

  defs.push(
    tool('wewrite_preview', '本地预览渲染（仅本地，不授予推送权限）。', {
      requestId: requestIdParam(),
      runId: runIdParam(),
      kind: { type: 'string' as const, enum: ['article', 'illustrated_article'] },
      expectedVersion: nullableHashParam('正文版本哈希或 null'),
      themeId: stringParam('主题 id'),
    }, (args, ctx) => service.toolPreview(args as never, ctx)),
  );

  return defs;
}
