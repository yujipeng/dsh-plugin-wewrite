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
import { getAssembled, runFacade, createHandler, RPC_CHANNEL } from '../lib/index.js';

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

  // D 项：经插件自己注册的 RPC 端点发起 run/create → run/start（同一 createHandler apply() 注册到路由）。
  const rpc = createHandler(assembled.service);
  const signal = new AbortController().signal;
  const peer = { principal: { kind: 'owner' } };

  const createResp = await rpc('run/create', {
    requestId: randomUUID(), topic: '人工智能对教育的影响', mode: 'draft', visualMode: 'none', themeId: 'professional-clean',
  }, signal, peer);
  if (createResp.ok !== true) throw new Error(`run/create 失败：${JSON.stringify(createResp.error)}`);
  const created = createResp.value;
  if (created.ok !== true) throw new Error(`run/create 业务失败：${JSON.stringify(created.error)}`);
  const runId = created.data.runId;
  log('info', `runId=${runId}`);

  // 安装 model selection（headless 兼容）+ startRun 走生产 workflow。
  const agents = ctx.get('agents');
  const agentDefaultModel = ctx.get('agentDefaultModel');
  const selection = agentDefaultModel?.currentSelection?.() ?? { provider: 'tcreditai', model: 'deepseek-flash' };

  // D 项：run/start 也经插件注册的 RPC 端点（createHandler）。
  const startResp = await rpc('run/start', {
    requestId: randomUUID(), runId, workspaceId: 'ws-e2e',
  }, signal, peer);
  if (startResp.ok !== true) throw new Error(`run/start 失败：${JSON.stringify(startResp.error)}`);
  const started = startResp.value;
  if (started.ok !== true) throw new Error(`run/start 业务失败：${JSON.stringify(started.error)}`);
  const binding = started.data.binding;
  const workflow = assembled.workflow;
  const handle = workflow.agentHandleOf(runId);
  const agent = handle?.agent;
  if (!agent) throw new Error('run/start 后无活跃 Agent');
  log('info', `sessionId=${binding.sessionId} agentId=${binding.agentId} workflowId=${binding.workflowId} generation=${binding.generation}`);

  // D 项：真实宿主 scope 双路径硬断言（缺工具列表必须失败，不得跳过）。
  const scopedTools = agent.ctx?.get?.('tools');
  if (!scopedTools || typeof scopedTools.schemas !== 'function') {
    throw new Error('scope 探针失败：拿不到 tools.schemas（必须失败，不得跳过）');
  }
  const schemas = scopedTools.schemas(agent);
  const names = Array.isArray(schemas) ? schemas.map((s) => s.name) : [];
  if (names.length === 0) throw new Error('scope 探针失败：可见工具列表为空');
  log('info', `scope visible tools=${JSON.stringify(names)}`);
  // 硬断言：继承危险工具不可见、reserved run_code 不可见、8 个 native 工具可见。
  for (const dangerous of ['bash', 'write', 'edit', 'str_replace_editor', 'subagent', 'delegate', 'run_code']) {
    if (names.includes(dangerous)) throw new Error(`scope 探针失败：危险工具 ${dangerous} 可见`);
  }
  const nativeTools = ['wewrite_run_start','wewrite_run_detail','wewrite_artifact_get','wewrite_run_step','wewrite_artifact_write','wewrite_review','wewrite_run_finish','wewrite_preview'];
  for (const t of nativeTools) {
    if (!names.includes(t)) throw new Error(`scope 探针失败：native 工具 ${t} 不可见`);
  }
  log('info', 'scope 硬断言通过：危险工具不可见、run_code 不可见、8 个 native 工具可见');

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
