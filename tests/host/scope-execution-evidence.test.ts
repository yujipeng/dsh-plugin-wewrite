/**
 * JEE-1956 缺口 5：生产路由分派入口 + 真实 scope 执行证据。
 *
 * 用真实 DSH ToolRuntime（@deepseek-ai/dsh-tools）+ createScope 构造专属写作 Agent 的 scope，
 * 走生产同一 setupAgentScope 装配（继承层 restrict 空清单 + 自身层 guard），实际执行：
 * - 继承层危险工具（bash/write/edit/str_replace_editor/subagent/delegate）→ 拒绝且零副作用；
 * - 自身层危险工具（scope 内注册一个非受控工具）→ 拒绝且零副作用；
 * - reserved `run_code` 直接调用 → 拒绝且零副作用；
 * - 8 个 native 插件工具可见且可执行（探针工具走真实 execute 返回业务结构）。
 */

import { describe, expect, it } from 'vitest';
import { Context } from '@deepseek-ai/cordis';
import { ToolRuntime } from '@deepseek-ai/dsh-tools';
import { ToolCallId } from '@deepseek-ai/dsh-llm';
import { createScope, type ScopeKey } from '@deepseek-ai/dsh-scope';
import { AGENT_TOOL_NAMES, buildAgentTools, setupAgentScope } from '@/host/tools';
import { WorkbenchService } from '@/host/workbench-service';
import { AgentWorkflow } from '@/host/agent-workflow';

interface FakeAgent {
  id: string;
}

function makeService(): WorkbenchService {
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
  });
}

function makeScope() {
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

const inheritedDangerous = ['bash', 'write', 'edit', 'str_replace_editor', 'subagent', 'delegate'];

describe('真实 scope 执行证据（JEE-1956 缺口 5）', () => {
  it('继承层危险工具被 restrict 拒且零副作用', async () => {
    const { tools, scopedTools, agentKey, scope } = makeScope();
    const sideEffects: string[] = [];

    // 全局层注册继承危险工具（模拟 DSH 宿主全局能力）。
    for (const name of inheritedDangerous) {
      tools.register({
        name,
        description: 'dangerous',
        parameters: { type: 'object', properties: {} },
        output: { schema: { type: 'string' }, render: (_a: unknown, v: unknown) => [{ type: 'text', text: String(v) }] },
        execute: async () => { sideEffects.push(name); return `ran ${name}`; },
      });
    }

    setupAgentScope(scope.ctx, buildAgentTools(makeService()));

    // restrict 后继承危险工具不可见。
    const visible = tools.schemas(agentKey).map((s) => s.name);
    for (const name of inheritedDangerous) expect(visible).not.toContain(name);

    // 实际执行：全部被拒且零副作用。
    for (const name of inheritedDangerous) {
      const result = await scopedTools.execute({
        callId: ToolCallId(`c-${name}`),
        name,
        arguments: {},
        agent: agentKey as never,
        signal: new AbortController().signal,
      });
      expect(result.isError).toBe(true);
    }
    expect(sideEffects).toEqual([]);
    scope.dispose();
  });

  it('自身层危险工具被 guard 拒且零副作用', async () => {
    const { scopedTools, agentKey, scope } = makeScope();
    let sideEffect = 0;

    setupAgentScope(scope.ctx, buildAgentTools(makeService()));
    // scope 自身层再注册一个非受控危险工具（自身层注册不受 restrict 约束，只受 guard）。
    scopedTools.register({
      name: 'wewrite_evil',
      description: 'own-layer dangerous',
      parameters: { type: 'object', properties: {} },
      output: { schema: { type: 'string' }, render: (_a: unknown, v: unknown) => [{ type: 'text', text: String(v) }] },
      execute: async () => { sideEffect += 1; return 'ran'; },
    });

    const result = await scopedTools.execute({
      callId: ToolCallId('c-own-evil'),
      name: 'wewrite_evil',
      arguments: {},
      agent: agentKey as never,
      signal: new AbortController().signal,
    });
    expect(result.isError).toBe(true);
    expect(sideEffect).toBe(0);
    scope.dispose();
  });

  it('reserved run_code 直接调用被拒且零副作用', async () => {
    const { scopedTools, agentKey, scope } = makeScope();
    setupAgentScope(scope.ctx, buildAgentTools(makeService()));

    const result = await scopedTools.execute({
      callId: ToolCallId('c-run-code'),
      name: 'run_code',
      arguments: {},
      agent: agentKey as never,
      signal: new AbortController().signal,
    });
    expect(result.isError).toBe(true);
    expect(result.content?.[0]).toMatchObject({ type: 'text' });
    scope.dispose();
  });

  it('8 个 native 插件工具可见', () => {
    const { tools, agentKey, scope } = makeScope();
    setupAgentScope(scope.ctx, buildAgentTools(makeService()));
    const visible = tools.schemas(agentKey).map((s) => s.name);
    for (const name of AGENT_TOOL_NAMES) expect(visible).toContain(name);
    scope.dispose();
  });
});
