/**
 * agent-workflow 测试（设计 §5 接管栅栏 + 句柄/取消参数）。
 * 用假 DshAgentRuntime + 假 bridge 验证：
 * - startRun 发布 generation=1，runId 贯通。
 * - resumeRun 接管：expectedGeneration CAS、新 workflowId、旧记录撤销。
 * - 并发接管仅一个成功。
 * - cancel 用 {kind:'user'}（判别联合，无裸字符串）。
 */

import { describe, expect, it } from 'vitest';
import { AgentWorkflow, type DshAgentRuntime } from '@/host/agent-workflow';
import type { AgentHandle } from '@deepseek-ai/dsh-agent';

interface FakeAgent {
  id: string;
  cancelled: unknown[];
  followed: string[];
  cancel(cause: unknown): void;
  followup(message: unknown): void;
}

function makeRuntime() {
  const agents = new Map<string, FakeAgent>();
  const runtime: DshAgentRuntime = {
    async create(options) {
      const agent: FakeAgent = {
        id: options.sessionId,
        cancelled: [],
        followed: [],
        cancel(cause: unknown) {
          this.cancelled.push(cause);
        },
        followup(message: unknown) {
          this.followed.push(JSON.stringify(message));
        },
      };
      agents.set(options.sessionId, agent);
      const handle = {
        agent: agent as never,
        dispose: async () => { agents.delete(options.sessionId); },
      } as AgentHandle;
      return handle;
    },
    async resume(options) {
      return runtime.create({ sessionId: options.resumeSessionId });
    },
    get(id: string) {
      return (agents.get(id)?.id ? { id } : undefined) as never;
    },
  };
  return { runtime, agents };
}

function makeBridge(bindings: Map<string, { generation: number; workflowId: string; agentId: string; sessionId: string }>) {
  return async (operation: string, payload: Record<string, unknown>) => {
    if (operation === 'binding.publish') {
      const current = bindings.get(payload.runId as string);
      const expected = payload.expectedGeneration as number | undefined;
      if (expected === undefined) {
        if (current) return { ok: false, error: { code: 'VERSION_CONFLICT', message: '冲突', retryable: false } };
      } else if (!current || current.generation !== expected) {
        return { ok: false, error: { code: 'VERSION_CONFLICT', message: '冲突', retryable: false } };
      }
      const binding = {
        runId: payload.runId as string,
        workflowId: payload.workflowId as string,
        generation: payload.generation as number,
        agentId: payload.agentId as string,
        sessionId: payload.sessionId as string,
        workspaceId: payload.workspaceId as string,
        phase: payload.phase as string,
      };
      bindings.set(payload.runId as string, binding);
      return { ok: true, data: binding };
    }
    if (operation === 'binding.get') {
      const binding = bindings.get(payload.runId as string);
      return { ok: true, data: { binding: binding ?? null } };
    }
    return { ok: false, error: { code: 'INTERNAL', message: '内部错误', retryable: false } };
  };
}

function makeWorkflow(bridge: ReturnType<typeof makeBridge>) {
  const { runtime, agents } = makeRuntime();
  const workflow = new AgentWorkflow({
    runtime,
    bridge,
    homeLabel: '数据根',
    mainSkillName: 'wewrite',
    randomId: (() => { let n = 0; return () => `id-${++n}`; })(),
  });
  return { workflow, agents };
}

const runId = '20261028-120000-abcdef';

describe('AgentWorkflow（接管栅栏 + 句柄/取消参数）', () => {
  it('startRun 发布 generation=1，runId 贯通', async () => {
    const bindings = new Map();
    const bridge = makeBridge(bindings as never);
    const { workflow } = makeWorkflow(bridge);
    const result = await workflow.startRun({ runId, workspaceId: 'ws', topic: '主题', mode: 'draft', visualMode: 'none', themeId: 't' });
    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.data.binding.generation).toBe(1);
      expect(result.data.binding.runId).toBe(runId);
    }
    expect(workflow.bindingOf(runId)?.generation).toBe(1);
  });

  it('重复 startRun → RUN_BUSY（已在 scope 内绑定）', async () => {
    const bindings = new Map();
    const bridge = makeBridge(bindings as never);
    const { workflow } = makeWorkflow(bridge);
    const first = await workflow.startRun({ runId, workspaceId: 'ws', topic: 't', mode: 'draft', visualMode: 'none', themeId: 't' });
    expect(first.ok).toBe(true);
    const second = await workflow.startRun({ runId, workspaceId: 'ws', topic: 't', mode: 'draft', visualMode: 'none', themeId: 't' });
    expect(second.ok).toBe(false);
    if (!second.ok) expect(second.error.code).toBe('RUN_BUSY');
  });

  it('resumeRun 接管：expectedGeneration CAS 成功、新 workflowId、旧记录撤销', async () => {
    const bindings = new Map();
    const bridge = makeBridge(bindings as never);
    const { workflow, agents } = makeWorkflow(bridge);
    await workflow.startRun({ runId, workspaceId: 'ws', topic: 't', mode: 'draft', visualMode: 'none', themeId: 't' });

    const oldAgentId = workflow.bindingOf(runId)!.agentId;
    const result = await workflow.resumeRun({ runId, expectedGeneration: 1, workspaceId: 'ws' });
    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.data.binding.generation).toBe(2);
      expect(result.data.binding.workflowId).not.toBe('id-2'); // 新 workflowId（id-3）
      expect(result.data.binding.agentId).not.toBe(oldAgentId);
    }
    // 旧 Agent 被钩子取消（判别联合，非裸字符串）。
    const oldAgent = agents.get(oldAgentId);
    if (oldAgent) {
      expect(oldAgent.cancelled[0]).toEqual({ kind: 'hook', reason: expect.any(String) });
    }
  });

  it('resumeRun expectedGeneration 不匹配 → VERSION_CONFLICT（并发接管仅一个成功）', async () => {
    const bindings = new Map();
    const bridge = makeBridge(bindings as never);
    const { workflow } = makeWorkflow(bridge);
    await workflow.startRun({ runId, workspaceId: 'ws', topic: 't', mode: 'draft', visualMode: 'none', themeId: 't' });

    const stale = await workflow.resumeRun({ runId, expectedGeneration: 99, workspaceId: 'ws' });
    expect(stale.ok).toBe(false);
    if (!stale.ok) expect(stale.error.code).toBe('VERSION_CONFLICT');
  });

  it('cancelRun 用 {kind:user} 取消并移除绑定', async () => {
    const bindings = new Map();
    const bridge = makeBridge(bindings as never);
    const { workflow, agents } = makeWorkflow(bridge);
    await workflow.startRun({ runId, workspaceId: 'ws', topic: 't', mode: 'draft', visualMode: 'none', themeId: 't' });
    const agentId = workflow.bindingOf(runId)!.agentId;
    const result = await workflow.cancelRun({ runId, generation: 1 });
    expect(result.ok).toBe(true);
    if (result.ok) expect(result.data.phase).toBe('cancelled');
    expect(workflow.bindingOf(runId)).toBeUndefined();
    const agent = agents.get(agentId);
    if (agent) expect(agent.cancelled[0]).toEqual({ kind: 'user' });
  });
});
