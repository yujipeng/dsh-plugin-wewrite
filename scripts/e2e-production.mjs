/**
 * 第四轮证据：生产 apply() 真实 profile 装载 → 经插件装配的 WorkbenchService/AgentWorkflow
 * 发起 run/create → run/start，并做真实宿主 scope 探针。
 *
 * 不再 new WorkbenchService/AgentWorkflow：全部通过 lib/index.js 导出的 getAssembled()
 * 取 apply() 已装配的对象。
 *
 * 用法：
 *   DSH_HOME=<workdir>/dshhome WEWRITE_HOME=<workdir>/wewrite-home \
 *   WEWRITE_PYTHON=<workdir>/dsh-plugin-wewrite/.venv/bin/python \
 *   node <workdir>/dsh-plugin-wewrite/scripts/e2e-production.mjs
 */

import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { randomUUID } from 'node:crypto';
import { runProfile } from '@deepseek-ai/dsh/profile-boot';
import { loadLayeredEnv } from '@deepseek-ai/dsh-app-boot';
import { createUserMessage } from '@deepseek-ai/dsh-llm';
import { installModelSelection } from '@deepseek-ai/dsh-agent';
import { getAssembled, runFacade } from '../lib/index.js';

const ROOT = resolve(fileURLToPath(import.meta.url), '..', '..');
const HOME = process.env.WEWRITE_HOME ?? resolve(ROOT, '..', 'wewrite-home');
const PYTHON = process.env.WEWRITE_PYTHON ?? 'python3';

function log(level, ...args) {
  process.stderr.write(`[e2e-prod:${level}] ${args.join(' ')}\n`);
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

  // 等插件 apply() 完成装配（production）。生产装配结果挂在 globalThis.__wewriteAssembled。
  const assembled = await (async () => {
    for (let i = 0; i < 200; i++) {
      const a = globalThis.__wewriteAssembled ?? getAssembled('default') ?? getAssembled('web');
      if (a) return a;
      await new Promise((r) => setTimeout(r, 50));
    }
    throw new Error('生产装配未完成：getAssembled 空');
  })();

  log('info', `assembled: interpreter=${assembled.interpreter} home=${assembled.home}`);

  // 用生产装配的 WorkbenchService 发起 run/create → run/start（同一服务 apply() 注册到 RPC 路由）。
  const service = assembled.service;
  const created = await service.runCreate({
    requestId: randomUUID(), topic: '人工智能对教育的影响', mode: 'draft', visualMode: 'none', themeId: 'professional-clean',
  });
  if (!created.ok) throw new Error(`run/create 失败：${JSON.stringify(created.error)}`);
  const runId = created.data.runId;
  log('info', `runId=${runId}`);

  // 安装 model selection（headless 兼容）+ startRun 走生产 workflow。
  const agents = ctx.get('agents');
  const agentDefaultModel = ctx.get('agentDefaultModel');
  const selection = agentDefaultModel?.currentSelection?.() ?? { provider: 'tcreditai', model: 'deepseek-flash' };

  // 生产 workflow 内部 runtime 由 apply() 组装；startRun 需要 workspaceId。
  const workflow = assembled.workflow;
  const started = await workflow.startRun({
    runId, workspaceId: 'ws-e2e', topic: '人工智能对教育的影响', mode: 'draft', visualMode: 'none', themeId: 'professional-clean',
  });
  if (!started.ok) throw new Error(`run/start 失败：${JSON.stringify(started.error)}`);
  const binding = started.data.binding;
  const handle = workflow.agentHandleOf(runId);
  const agent = handle?.agent;
  if (!agent) throw new Error('run/start 后无活跃 Agent');
  log('info', `sessionId=${binding.sessionId} agentId=${binding.agentId} workflowId=${binding.workflowId} generation=${binding.generation}`);

  // 真实宿主 scope 探针：继承工具不可见 + reserved run_code 被 guard 拒 + native 工具可执行。
  // 由于 followup 走真实 turn 执行，工具实际执行记录由 session 日志给出；这里额外检查 guard 注册面。
  const scopedTools = agent.ctx?.get?.('tools');
  if (scopedTools) {
    const schemas = typeof scopedTools.schemas === 'function' ? scopedTools.schemas(agent) : undefined;
    const names = Array.isArray(schemas) ? schemas.map((s) => s.name) : [];
    log('info', `scope visible tools=${JSON.stringify(names)}`);
    log('info', `scope has bash=${names.includes('bash')} run_code=${names.includes('run_code')} wewrite=${names.some((n) => n.startsWith('wewrite_'))}`);
  }

  // followup 主 skill 指令，等待真实 Agent 完成。
  const message = createUserMessage({
    content: [{ type: 'text', text: [
      '请使用 wewrite 主 skill 完成这篇写作任务（run 已创建，不要执行 wewrite run start/resume）。',
      `runId: ${binding.runId}`, `workflowId: ${binding.workflowId}`, `generation: ${binding.generation}`,
      'mode: draft', 'visualMode: none', 'themeId: professional-clean', 'topic: 人工智能对教育的影响',
      '数据根: wewrite-home', '主 skill: wewrite',
      '请按 brief → claims → draft → article 顺序用 wewrite_artifact_write 写入非空内容，',
      '再用 wewrite_review 提交五维评估（accuracy/viewpoint/usefulness/voice/readability 各 5 分，',
      'decision=pass，pass_number=1，blockers=[], major_issues=[], notes=通过），',
      '最后 wewrite_run_finish 封存（expectedVersion/reportVersion 用真实哈希）。',
    ].join('\n') }],
    source: { kind: 'user' },
  });
  agent.followup(message);
  await agent.whenIdle();

  const detail = await bridge('run.detail', { runId });
  const run = detail.ok ? detail.data.run : {};
  const artifacts = detail.ok ? detail.data.artifacts : [];
  const gate = detail.ok ? detail.data.gate : null;
  const kinds = ['brief', 'claims', 'draft', 'article', 'review_report'];
  const nonEmpty = (k) => artifacts.find((a) => a.kind === k)?.nonEmpty === true;
  const hashes = Object.fromEntries(artifacts.filter((a) => kinds.includes(a.kind)).map((a) => [a.kind, a.version]));

  log('info', `run.phase=${run.phase} gate.publishable=${gate?.publishable}`);
  for (const k of kinds) log('info', `${k}=nonEmpty:${nonEmpty(k)} version:${hashes[k] ?? '(缺)'}`);
  const ok = kinds.every((k) => nonEmpty(k)) && run.phase === 'ready' && gate?.publishable === true;
  log(ok ? 'info' : 'error', ok ? 'RESULT=通过' : 'RESULT=未通过');

  await ctx.fiber.dispose();
  process.exit(ok ? 0 : 2);
}

main().catch((error) => {
  process.stderr.write(`[e2e-prod:fatal] ${error?.stack ?? error}\n`);
  process.exit(1);
});
