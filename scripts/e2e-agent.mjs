/**
 * 真实 DSH Agent 一键写作闭环证据脚本（返修 7）。
 *
 * 用法（在任务工作目录）：
 *   DSH_HOME=<workdir>/dshhome \
 *   WEWRITE_HOME=<workdir>/wewrite-home \
 *   WEWRITE_PYTHON=<workdir>/dsh-plugin-wewrite/.venv/bin/python \
 *   node <workdir>/dsh-plugin-wewrite/scripts/e2e-agent.mjs
 *
 * 流程：boot headless profile（已安装 dsh-plugin-wewrite）→ facade run.create →
 * AgentWorkflow.startRun（真实 ctx.agents.create，setup 内注册受控工具 + restrict + guard）→
 * agent.followup 主 skill 指令 → 等待 Agent 执行 wewrite_* 工具 → 读回 run.detail，
 * 输出 sessionId/runId 对照与五类非空产物哈希。
 */

import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { randomUUID } from 'node:crypto';
import { runProfile } from '@deepseek-ai/dsh/profile-boot';
import { loadLayeredEnv } from '@deepseek-ai/dsh-app-boot';
import { createUserMessage } from '@deepseek-ai/dsh-llm';
import { installModelSelection } from '@deepseek-ai/dsh-agent';
import { AgentWorkflow, WorkbenchService, runFacade, buildAgentTools, setToolService, controlledToolGuard, INHERITED_TOOL_ALLOWLIST } from '../lib/index.js';

const ROOT = resolve(fileURLToPath(import.meta.url), '..', '..');
const HOME = process.env.WEWRITE_HOME ?? resolve(ROOT, '..', 'wewrite-home');
const PYTHON = process.env.WEWRITE_PYTHON ?? 'python3';

function log(level, ...args) {
  process.stderr.write(`[e2e-agent:${level}] ${args.join(' ')}\n`);
}

async function bridge(operation, payload) {
  const result = await runFacade(
    { operation, payload, requestId: payload.requestId ?? randomUUID() },
    { interpreter: PYTHON, home: HOME },
  );
  if (result.issue) return { ok: false, error: { code: result.issue.code, message: result.issue.message, retryable: false } };
  if (result.timedOut) return { ok: false, error: { code: 'TIMEOUT', message: '操作超时', retryable: true } };
  if (!result.response) return { ok: false, error: { code: 'INTERNAL', message: '内部错误', retryable: false } };
  return result.response.ok ? { ok: true, data: result.response.data } : { ok: false, error: result.response.error };
}

async function main() {
  const { ctx } = await runProfile({ environment: loadLayeredEnv('dsh'), profile: 'web', patchFiles: [], args: [] });
  const agents = ctx.get('agents');

  // 1) run.create：建 run（runId 五处贯通的第一处）。
  const created = await bridge('run.create', {
    requestId: randomUUID(),
    topic: '人工智能对教育的影响',
    mode: 'draft',
    visualMode: 'none',
    themeId: 'professional-clean',
  });
  if (!created.ok) throw new Error(`run.create 失败：${JSON.stringify(created.error)}`);
  const runId = created.data.runId;
  log('info', `runId=${runId}`);

  // 2) 组装真实 WorkbenchService（工具注册由 scopeSetup 在真实 agentCtx 上执行）。
  const service = new WorkbenchService({
    bridge,
    workflow: undefined,
    randomId: () => randomUUID(),
    replay: new Map(),
  });
  const toolDefs = buildAgentTools(service);
  setToolService(service);

  // 3) AgentWorkflow.startRun：真实 ctx.agents.create，setup 内注册工具 + restrict + guard。
  //    并安装 model selection（与 headless-runner 一致，避免 prompt 变量缺失）。
  const agentDefaultModel = ctx.get('agentDefaultModel');
  const selection = agentDefaultModel?.currentSelection?.() ?? { provider: 'tcreditai', model: 'deepseek-flash' };
  const workflow = new AgentWorkflow({
    runtime: {
      create: async (opts) => agents.create({
        sessionId: opts.sessionId,
        agentOptions: { provider: selection.provider, model: selection.model },
        setup: (agentCtx, agent) => {
          installModelSelection(agentCtx, { current: selection, assembled: undefined });
          return opts.setup?.(agentCtx, agent);
        },
      }),
      resume: async (opts) => agents.resume({
        resumeSessionId: opts.resumeSessionId,
        agentOptions: { provider: selection.provider, model: selection.model },
        setup: (agentCtx, agent) => {
          installModelSelection(agentCtx, { current: selection, assembled: undefined });
          return opts.setup?.(agentCtx, agent);
        },
      }),
      get: (id) => agents.get(id),
    },
    bridge,
    scopeSetup: (agentCtx) => {
      const agentTools = agentCtx.get?.('tools');
      if (typeof agentTools?.register !== 'function') return;
      for (const def of toolDefs) agentTools.register(def);
      if (typeof agentTools.restrict === 'function') {
        agentTools.restrict({ allow: [...INHERITED_TOOL_ALLOWLIST] });
      }
      if (typeof agentTools.guard === 'function') {
        agentTools.guard(controlledToolGuard);
      }
    },
    homeLabel: 'wewrite-home',
    mainSkillName: 'wewrite',
    randomId: () => randomUUID(),
    onError: (m) => log('warn', m),
  });
  (service).options.workflow = workflow;

  const started = await workflow.startRun({
    runId,
    workspaceId: 'ws-e2e',
    topic: '人工智能对教育的影响',
    mode: 'draft',
    visualMode: 'none',
    themeId: 'professional-clean',
  });
  if (!started.ok) throw new Error(`run/start 失败：${JSON.stringify(started.error)}`);
  const binding = started.data.binding;
  const handle = workflow.agentHandleOf(runId);
  const agent = handle?.agent;
  if (!agent) throw new Error('run/start 后无活跃 Agent 句柄');
  log('info', `sessionId=${binding.sessionId} agentId=${binding.agentId} workflowId=${binding.workflowId} generation=${binding.generation}`);

  // 观察 session 事件：记录 tool_call 名称，作为「工具实际执行记录」证据。
  const toolCalls = [];
  const stopToolCalls = typeof ctx.on === 'function'
    ? ctx.on('session/event', (session, event) => {
        if (event?.type === 'tool/call') {
          toolCalls.push(event?.toolName ?? event?.data?.name ?? '');
        }
      })
    : undefined;

  // 4) followup 主 skill 指令，等待真实 Agent 完成（工具执行 → finish → ready）。
  const message = createUserMessage({
    content: [{ type: 'text', text: [
      '请使用 wewrite 主 skill 完成这篇写作任务（run 已创建，不要执行 wewrite run start/resume）。',
      `runId: ${binding.runId}`,
      `workflowId: ${binding.workflowId}`,
      `generation: ${binding.generation}`,
      'mode: draft',
      'visualMode: none',
      'themeId: professional-clean',
      'topic: 人工智能对教育的影响',
      '数据根: wewrite-home',
      '主 skill: wewrite',
      '请按 brief → claims → draft → article 顺序用 wewrite_artifact_write 写入非空内容。',
      '【正文必须是两段】中间只一个空行，两段都尽量短；第一段是结论句，第二段是一个事实句。article_span 用「该段在全文中的码点起止下标」（从 0 开始，左闭右开）。',
      '产物填写模板（editorial_schema: 2，严格按此字段形状，不要自创字段）：',
      '正文示例（你写自己的两句话）：「先做窄场景，再逐步扩张。」\\n\\n「试点已完成。」',
      'sources.yaml：version: 1、editorial_schema: 2、sources: [{id: s1, status: verified, url: https://example.org/report, claim: <与 evidence.text 相同的句子>, evidence: {text: <第二段原文>, sha256: 可省略由服务端补齐, locator: 第一节}}]',
      'brief.yaml：version: 1、editorial_schema: 2、goal: {takeaway: <第一段原文>, action: \"\"}、delivery: {article_hash: <artifact.get(article).version>, anchors: [{field: takeaway, article_span: {start: <第一段起始>, end: <第一段结束>}}]}',
      'claims.yaml：version: 1、editorial_schema: 2、article_hash: <同上>、claims: [{id: C1, type: fact, text: <第二段原文>, article_span: {start: <第二段起始>, end: <第二段结束>}, source_ids: [s1], evidence_refs: [{source_id: s1, evidence_sha256: <回读 sources 的 evidence.sha256>, mode: verbatim, quote: <第二段原文>}]}]、blocks: [{article_span: {start: <第一段起始>, end: <第一段结束>}, kind: other, claim_ids: []}, {article_span: {start: <第二段起始>, end: <第二段结束>}, kind: fact, claim_ids: [C1]}]',
      'article_span 的 end 必须等于 start + 该段字符数（码点，不是字节）。',
      '再调用 wewrite_review 提交五维评估（accuracy/viewpoint/usefulness/voice/readability 各 5 分，decision=pass，pass_number=1，blockers=[], major_issues=[], notes=通过）。',
      '审稿 pass 只证明「引用链与声明结构完整」，未验证语义与来源真实性；最后 wewrite_run_finish 封存（expectedVersion/reportVersion 用真实哈希）。',
    ].join('\n') }],
    source: { kind: 'user' },
  });
  agent.followup(message);

  // 5) 等待 quiescence（真实 turn 执行完成）。
  await agent.whenIdle();

  // 6) 读回产物与状态。
  const detail = await bridge('run.detail', { runId });
  const run = detail.ok ? detail.data.run : {};
  const artifacts = detail.ok ? detail.data.artifacts : [];
  const gate = detail.ok ? detail.data.gate : null;

  const kinds = ['brief', 'claims', 'draft', 'article', 'review_report'];
  const nonEmpty = (k) => artifacts.find((a) => a.kind === k)?.nonEmpty === true;
  const hashes = Object.fromEntries(artifacts.filter((a) => kinds.includes(a.kind)).map((a) => [a.kind, a.version]));

  log('info', `run.phase=${run.phase ?? '(无)'}`);
  log('info', `gate.publishable=${gate?.publishable ?? '(无)'}`);
  for (const k of kinds) log('info', `${k}=nonEmpty:${nonEmpty(k)} version:${hashes[k] ?? '(缺)'}`);

  const ok = kinds.every((k) => nonEmpty(k)) && run.phase === 'ready' && gate?.publishable === true;
  log(ok ? 'info' : 'error', ok ? 'RESULT=通过' : 'RESULT=未通过');
  log('info', `toolCalls=${JSON.stringify(toolCalls)}`);
  stopToolCalls?.();

  await ctx.fiber.dispose();
  process.exit(ok ? 0 : 2);
}

main().catch((error) => {
  process.stderr.write(`[e2e-agent:fatal] ${error?.stack ?? error}\n`);
  process.exit(1);
});
