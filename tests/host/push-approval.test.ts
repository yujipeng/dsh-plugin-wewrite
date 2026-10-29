/**
 * S4 双闸与逐篇确认（host 侧）：push-approval 第一闸 + 工具注册条件 + 执行令牌证据。
 *
 * 覆盖：
 * - pushEnabled=false 时 AGENT_TOOL_NAMES 无 wewrite_push_draft；审批服务缺失时 pushArmed=false。
 * - 第一闸 pre-execute：精确匹配 wewrite_push_draft 返回 ask，其他工具 next，永不 allow。
 * - 真实调度链两条强制断言（执行令牌不跨 exec 复用 + 第二次独立 asked）。
 * - 真实 transport 接线（配置 wechat → draft.send 收到 wechat；缺凭据 fail-closed）。
 */

import { describe, expect, it } from 'vitest';
import { Context } from '@deepseek-ai/cordis';
import { ToolRuntime, type ToolExecution } from '@deepseek-ai/dsh-tools';
import { ToolCallId } from '@deepseek-ai/dsh-llm';
import { createScope, type ScopeKey } from '@deepseek-ai/dsh-scope';
import {
  PUSH_TOOL_NAME,
  installPushPreExecute,
  probeApproval,
  pushAskReason,
} from '@/host/push-approval';
import { AGENT_TOOL_NAMES, buildAgentTools, buildPushTool, CONTROLLED_TOOL_SET, setupAgentScope } from '@/host/tools';
import { WorkbenchService } from '@/host/workbench-service';
import { AgentWorkflow } from '@/host/agent-workflow';

function makeService(pushOpts: {
  pushEnabled?: () => boolean;
  pushArmed?: () => boolean;
  publishTransport?: () => 'fake' | 'wechat';
  resolveCredentials?: () => Promise<Record<string, string> | undefined>;
} = {}) {
  const workflow = new AgentWorkflow({
    runtime: {
      create: async () => { throw new Error('agents 缺失'); },
      resume: async () => { throw new Error('agents 缺失'); },
      get: () => undefined,
    },
    bridge: async () => ({ ok: false, error: { code: 'INTERNAL', message: '内部错误', retryable: false } }),
    homeLabel: '数据根',
    mainSkillName: 'wewrite',
    randomId: () => 'id',
  });
  return new WorkbenchService({
    bridge: async () => ({ ok: false, error: { code: 'INTERNAL', message: '内部错误', retryable: false } }),
    workflow,
    randomId: () => 'id',
    pushEnabled: pushOpts.pushEnabled ?? (() => false),
    pushArmed: pushOpts.pushArmed ?? (() => false),
    publishTransport: pushOpts.publishTransport,
    resolveCredentials: pushOpts.resolveCredentials,
  });
}

/** 构造一个真实 ToolRuntime + 专属 Agent scope（与生产 setupAgentScope 同构）。 */
function makeToolRuntime() {
  const root = new Context();
  root.provide('systemPrompt', {
    tools: () => () => undefined,
    section: () => () => undefined,
    getSectionOrder: () => 0,
  });
  root.provide('logger', { warn: () => undefined, info: () => undefined, error: () => undefined });
  const tools = new ToolRuntime(root);
  const agentKey: ScopeKey = { id: `agent-scope-${Math.random()}` };
  const scope = createScope(root, agentKey);
  const scopedTools = scope.ctx.get('tools') as ToolRuntime;
  return { root, tools, scopedTools, agentKey, scope };
}

describe('S4 推送工具注册条件（第一闸门禁）', () => {
  it('pushEnabled=false 时 AGENT_TOOL_NAMES 不含 wewrite_push_draft', () => {
    expect([...AGENT_TOOL_NAMES]).not.toContain(PUSH_TOOL_NAME);
    // 常驻工具 8 个，推送工具单独条件注册。
    expect(AGENT_TOOL_NAMES).toHaveLength(8);
  });

  it('buildPushTool 产出 wewrite_push_draft，且 guard 白名单包含它', () => {
    const def = buildPushTool(makeService());
    expect(def.name).toBe(PUSH_TOOL_NAME);
    expect(CONTROLLED_TOOL_SET.has(PUSH_TOOL_NAME)).toBe(true);
  });

  it('审批服务缺失/无效时 probeApproval 返回 undefined（不注册推送工具）', () => {
    expect(probeApproval(undefined)).toBeUndefined();
    expect(probeApproval({})).toBeUndefined();
    expect(probeApproval({ request: 'not-a-function' })).toBeUndefined();
  });

  it('审批服务就绪时 probeApproval 返回可用运行时', () => {
    const runtime = { request: async () => 'allowed-once' as const };
    expect(probeApproval(runtime)).toBe(runtime);
  });
});

describe('第一闸 pre-execute（ask 永不 allow）', () => {
  function makeCtx() {
    const listeners: Record<string, unknown> = {};
    return {
      listeners,
      on(name: string, listener: unknown) {
        listeners[name] = listener;
        return () => { delete listeners[name]; };
      },
    };
  }

  it('精确匹配 wewrite_push_draft 返回 ask；其他工具 next()', async () => {
    const ctx = makeCtx();
    installPushPreExecute(ctx as never, () => '推送微信草稿（仅草稿）');
    const listener = ctx.listeners['tools/pre-execute'] as (exec: unknown, next: () => Promise<unknown>) => Promise<unknown>;

    let asked = false;
    const result = await listener({ name: PUSH_TOOL_NAME }, async () => {
      asked = true;
      return { kind: 'allow' };
    });
    expect(result).toEqual({ kind: 'ask', reason: '推送微信草稿（仅草稿）' });
    expect(asked).toBe(false);

    // 其他工具 next()，不拦截。
    const other = await listener({ name: 'wewrite_preview' }, async () => ({ kind: 'allow' }));
    expect(other).toEqual({ kind: 'allow' });
  });

  it('pushAskReason 含标题/账号/版本/封面/门禁/仅草稿', () => {
    const reason = pushAskReason({
      title: '测试标题',
      accountLabel: '账号A',
      articleVersion: 'a'.repeat(64),
      coverHash: 'b'.repeat(64),
      gate: '审稿通过',
    });
    expect(reason).toContain('仅草稿');
    expect(reason).toContain('测试标题');
    expect(reason).toContain('账号A');
    expect(reason).toContain('a'.repeat(64));
    expect(reason).toContain('b'.repeat(64));
    expect(reason).toContain('审稿通过');
  });
});

describe('真实调度链：执行令牌不跨 exec 复用（S4 强制断言 1）', () => {
  it('同一 run/generation 两次有效 intent，走两次真实 tools.execute，token 不相等 + 第二次独立 asked', async () => {
    const { root, scopedTools, agentKey, scope } = makeToolRuntime();

    // 真实审批服务：记录每次 ask 的 toolName+callId，返回 allowed-once。
    const asks: string[] = [];
    root.provide('approval', {
      request: async (req: { toolName: string; callId?: unknown }) => {
        asks.push(`${req.toolName}:${String(req.callId ?? '')}`);
        return 'allowed-once';
      },
    });

    // 生产同构：installPushPreExecute 注册在 scoped Context（agent scope）。
    const tokens: unknown[] = [];
    installPushPreExecute(scope.ctx as never, () => '推送微信草稿（仅草稿）');

    // 工具注册进 scoped tools，guard 放行 push 工具。
    scopedTools.register(buildPushTool(makeService()));
    // 单调 guard：允许 native 工具 + 记录 exec.token（第二闸入口观察 token）。
    scopedTools.guard((execution) => {
      if (execution.name === PUSH_TOOL_NAME) {
        tokens.push((execution as ToolExecution).token);
        return undefined;
      }
      return CONTROLLED_TOOL_SET.has(execution.name) ? undefined : '拒绝';
    });

    const input = { runId: '20261029-120000-abcdef', intentId: 'intent-1', expectedVersion: null };
    const r1 = await scopedTools.execute({
      callId: ToolCallId('call-1'),
      name: PUSH_TOOL_NAME,
      arguments: input,
      agent: agentKey as never,
      signal: new AbortController().signal,
    });
    const r2 = await scopedTools.execute({
      callId: ToolCallId('call-2'),
      name: PUSH_TOOL_NAME,
      arguments: { ...input, intentId: 'intent-2' },
      agent: agentKey as never,
      signal: new AbortController().signal,
    });

    // 两次都经过 ask → allowed-once → guard → execute（至少两 ask，toolName 一致）。
    expect(asks).toHaveLength(2);
    for (const a of asks) expect(a).toContain(PUSH_TOOL_NAME);
    // 第二次独立 asked（不同 callId）。
    expect(asks[0]).toContain('call-1');
    expect(asks[1]).toContain('call-2');
    // 两次 token 不相等（宿主每次 createExecutionToken 新铸）。
    expect(tokens).toHaveLength(2);
    expect(tokens[0]).not.toBe(tokens[1]);
    expect(typeof tokens[0]).toBe('symbol');
    expect(typeof tokens[1]).toBe('symbol');
    scope.dispose();
  });
});

describe('真实 transport 接线（S4 返修 1）', () => {
  it('配置 wechat → draft.send 收到 transport=wechat；缺凭据 fail-closed（不静默退回 fake）', async () => {
    const sent: Array<{ transport: unknown; credentials: unknown }> = [];
    const service = new WorkbenchService({
      bridge: async (operation, payload) => {
        if (operation === 'draft.send') {
          sent.push({ transport: (payload as Record<string, unknown>).transport, credentials: (payload as Record<string, unknown>).credentials });
          return { ok: true, data: { operationId: 'op-1', status: 'succeeded', mediaId: 'real-media-1', dryRun: false, counters: { wechatApiCalls: 2, remoteFetchCalls: 0 } } };
        }
        return { ok: false, error: { code: 'INTERNAL', message: '内部错误', retryable: false } };
      },
      workflow: new AgentWorkflow({
        runtime: { create: async () => { throw new Error('x'); }, resume: async () => { throw new Error('x'); }, get: () => undefined },
        bridge: async () => ({ ok: false, error: { code: 'INTERNAL', message: 'x', retryable: false } }),
        homeLabel: '数据根',
        mainSkillName: 'wewrite',
        randomId: () => 'id',
      }),
      randomId: () => 'id',
      pushEnabled: () => true,
      pushArmed: () => true,
      publishTransport: () => 'wechat',
      resolveCredentials: async () => ({ appid: 'wx-app', secret: 'wx-secret' }),
    });

    const result = await service.toolPushDraft(
      { runId: '20261029-120000-abcdef', intentId: 'intent-1', expectedVersion: null },
      { runId: '20261029-120000-abcdef', workflowId: 'wf-1', generation: 1 },
    );
    expect(result.ok).toBe(true);
    expect(sent).toHaveLength(1);
    expect(sent[0].transport).toBe('wechat');
    expect(sent[0].credentials).toEqual({ appid: 'wx-app', secret: 'wx-secret' });
  });

  it('wechat 但缺凭据 → fail-closed，不触发 draft.send', async () => {
    let calls = 0;
    const service = new WorkbenchService({
      bridge: async () => { calls += 1; return { ok: true, data: {} }; },
      workflow: new AgentWorkflow({
        runtime: { create: async () => { throw new Error('x'); }, resume: async () => { throw new Error('x'); }, get: () => undefined },
        bridge: async () => ({ ok: false, error: { code: 'INTERNAL', message: 'x', retryable: false } }),
        homeLabel: '数据根',
        mainSkillName: 'wewrite',
        randomId: () => 'id',
      }),
      randomId: () => 'id',
      pushEnabled: () => true,
      pushArmed: () => true,
      publishTransport: () => 'wechat',
      resolveCredentials: async () => undefined,
    });

    const result = await service.toolPushDraft(
      { runId: '20261029-120000-abcdef', intentId: 'intent-1', expectedVersion: null },
      { runId: '20261029-120000-abcdef', workflowId: 'wf-1', generation: 1 },
    );
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.error.code).toBe('APPROVAL_UNAVAILABLE');
    expect(calls).toBe(0);
  });

  it('未配置 publishTransport（默认 fake）→ draft.send 收到 fake', async () => {
    const sent: string[] = [];
    const service = new WorkbenchService({
      bridge: async (operation, payload) => {
        if (operation === 'draft.send') {
          sent.push(String((payload as Record<string, unknown>).transport));
          return { ok: true, data: { operationId: 'op-1', status: 'succeeded', mediaId: 'fake-media-1', dryRun: true, counters: { wechatApiCalls: 0, remoteFetchCalls: 0 } } };
        }
        return { ok: false, error: { code: 'INTERNAL', message: 'x', retryable: false } };
      },
      workflow: new AgentWorkflow({
        runtime: { create: async () => { throw new Error('x'); }, resume: async () => { throw new Error('x'); }, get: () => undefined },
        bridge: async () => ({ ok: false, error: { code: 'INTERNAL', message: 'x', retryable: false } }),
        homeLabel: '数据根',
        mainSkillName: 'wewrite',
        randomId: () => 'id',
      }),
      randomId: () => 'id',
      pushEnabled: () => true,
      pushArmed: () => true,
    });

    const result = await service.toolPushDraft(
      { runId: '20261029-120000-abcdef', intentId: 'intent-1', expectedVersion: null },
      { runId: '20261029-120000-abcdef', workflowId: 'wf-1', generation: 1 },
    );
    expect(result.ok).toBe(true);
    expect(sent).toEqual(['fake']);
  });
});
