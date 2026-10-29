/**
 * agent-workflow.ts（设计 §2/§5 范围第 3 条）：创建/恢复/取消 Agent 与 run 绑定，落实接管栅栏。
 *
 * 关键约束（已定死）：
 * - `agents.create()` 返回 AgentHandle：必须 `handle.agent` 取 Agent，持有 `handle.dispose()`。
 * - `Agent.cancel()` 首参是 AgentCancelCause 判别联合，不传裸字符串。
 * - 接管栅栏：CAS + 旧绑定失效 + generation 原子递增 + 撤销旧 intent（facade binding.publish
 *   在 run 锁内 CAS 持久化，是唯一权威 fencing token），再发布新 Binding；新 workflowId。
 * - 每次工具执行从真实 exec.agent 查绑定；generation 取服务端绑定，不信模型自报。
 */

import type { Agent, AgentHandle } from '@deepseek-ai/dsh-agent';
import type { AgentCancelCause } from '@deepseek-ai/dsh-session';
import { createUserMessage } from '@deepseek-ai/dsh-llm';
import type { Binding } from '../shared/contract';
import { businessFailure, businessSuccess, type BusinessResult } from './envelope';
import { addSecrets, redactError } from './redaction';
import type { WorkbenchBridge } from './workbench-service';

/** Agent 运行时最小消费面（对标本机 DSH 0.2.1-rc.4）。测试注入假实现。 */
export interface DshAgentRuntime {
  create(options: {
    sessionId: string;
    setup?: (agentCtx: unknown, agent: Agent) => void | Promise<void>;
  }): Promise<AgentHandle>;
  /** 恢复/接管：以现有 session 重建 Agent。 */
  resume(options: {
    resumeSessionId: string;
    setup?: (agentCtx: unknown, agent: Agent) => void | Promise<void>;
  }): Promise<AgentHandle>;
  get(id: string): Agent | undefined;
}

/** 在专属写作 Agent 的 scope setup 里注册工具/guard/restrict 的装配闭包（宿主装配层提供）。 */
export type AgentScopeSetup = (agentCtx: unknown, agent: Agent) => void | Promise<void>;

export interface WorkflowBindingRecord {
  readonly binding: Binding;
  readonly handle: AgentHandle;
  /** 本次工作流的 intent 撤销表（S3 无真实推送，仍按设计记录）。 */
  readonly revokedIntents: Set<string>;
}

export interface AgentWorkflowOptions {
  readonly runtime: DshAgentRuntime;
  readonly bridge: WorkbenchBridge;
  /** 专属 Agent 的 scope 装配（注册工具/guard/restrict），在 setup 里调用。 */
  readonly scopeSetup?: AgentScopeSetup;
  /** 展示给 Agent 的 WEWRITE_HOME 标识（脱敏，不泄露绝对路径）。 */
  readonly homeLabel: string;
  readonly mainSkillName: string;
  readonly randomId: () => string;
  readonly onError?: (message: string) => void;
}

export interface StartRunInput {
  readonly runId: string;
  readonly workspaceId: string;
  readonly topic: string;
  readonly mode: 'draft' | 'complete';
  readonly visualMode: string;
  readonly themeId: string;
}

export interface ResumeRunInput {
  readonly runId: string;
  readonly expectedGeneration: number;
  readonly workspaceId: string;
}

export interface CancelRunInput {
  readonly runId: string;
  readonly generation: number;
}

export class AgentWorkflow {
  private readonly records = new Map<string, WorkflowBindingRecord>();

  constructor(private readonly options: AgentWorkflowOptions) {}

  bindingOf(runId: string): Binding | undefined {
    return this.records.get(runId)?.binding;
  }

  /** 测试/证据用：取某 run 的真实 Agent 句柄（生产代码不暴露）。 */
  agentHandleOf(runId: string): AgentHandle | undefined {
    return this.records.get(runId)?.handle;
  }

  allBindings(): Binding[] {
    return [...this.records.values()].map((r) => r.binding);
  }

  /** 每次工具入场：从真实 agent.id 查绑定，generation 取服务端绑定。 */
  bindingForAgent(agentId: string): Binding | undefined {
    for (const record of this.records.values()) {
      if (record.binding.agentId === agentId) return record.binding;
    }
    return undefined;
  }

  /** 发布绑定（facade CAS 持久化是唯一权威），失败即业务失败。 */
  private async publishBinding(input: {
    runId: string;
    workflowId: string;
    generation: number;
    agentId: string;
    sessionId: string;
    workspaceId: string;
    phase: Binding['phase'];
    expectedGeneration?: number;
  }): Promise<Binding> {
    const resp = await this.options.bridge('binding.publish', {
      requestId: this.options.randomId(),
      runId: input.runId,
      workflowId: input.workflowId,
      generation: input.generation,
      agentId: input.agentId,
      sessionId: input.sessionId,
      workspaceId: input.workspaceId,
      phase: input.phase,
      ...(input.expectedGeneration !== undefined ? { expectedGeneration: input.expectedGeneration } : {}),
    });
    if (resp.ok === false) {
      const err = resp.error as { code?: string };
      throw new Error(String(err?.code ?? 'INTERNAL'));
    }
    const data = resp.data as Record<string, unknown>;
    return {
      runId: data.runId as string,
      agentId: data.agentId as string,
      sessionId: data.sessionId as string,
      workspaceId: data.workspaceId as string,
      workflowId: data.workflowId as string,
      generation: data.generation as number,
      phase: data.phase as Binding['phase'],
    };
  }

  async startRun(input: StartRunInput): Promise<BusinessResult<{ binding: Binding }>> {
    try {
      const existing = this.records.get(input.runId);
      if (existing) {
        return businessFailure('RUN_BUSY', '该写作任务已被写作会话占用');
      }
      const sessionId = this.options.randomId();
      const workflowId = this.options.randomId();

      // setup 只准备尚未发布的绑定：注册工具/限制/skills/guard 由宿主装配层通过 scopeSetup 完成；
      // 绑定在创建成功后发布（发布提交点）。
      const handle = await this.options.runtime.create({
        sessionId,
        setup: (agentCtx, agent) => this.options.scopeSetup?.(agentCtx, agent),
      });
      const agent = handle.agent;

      let binding: Binding;
      try {
        binding = await this.publishBinding({
          runId: input.runId,
          workflowId,
          generation: 1,
          agentId: agent.id,
          sessionId,
          workspaceId: input.workspaceId,
          phase: 'starting',
        });
      } catch (error) {
        await handle.dispose().catch(() => undefined);
        if (error instanceof Error && error.message === 'VERSION_CONFLICT') {
          return businessFailure('RUN_BUSY', '该写作任务已被绑定');
        }
        return businessFailure('INTERNAL', '绑定发布失败');
      }

      this.records.set(input.runId, { binding, handle, revokedIntents: new Set() });

      // followup：消息含 runId/workflowId/generation/模式/主题/home 显示标识/主 skill 名。
      const message = createUserMessage({
        content: [{ type: 'text', text: this.followupText(input, binding) }],
        source: { kind: 'user' },
      });
      agent.followup(message);

      return businessSuccess({ binding });
    } catch (error) {
      this.options.onError?.(redactError(error));
      return businessFailure('INTERNAL', '启动写作会话失败');
    }
  }

  async resumeRun(input: ResumeRunInput): Promise<BusinessResult<{ binding: Binding; resumedExisting: boolean }>> {
    try {
      const record = this.records.get(input.runId);
      // 返修二轮-2：从持久化 binding 取原 sessionId（重启后 records 清空，只有 facade 持久化）。
      const persisted = await this.persistedBinding(input.runId);
      const currentGeneration = record?.binding.generation ?? persisted?.generation ?? null;
      if (currentGeneration === null) {
        return businessFailure('FORBIDDEN', 'run 未绑定写作会话');
      }
      if (input.expectedGeneration !== currentGeneration) {
        return businessFailure('VERSION_CONFLICT', '接管代次冲突，请刷新后重试');
      }

      // 接管栅栏：新 workflowId + generation+1；先停旧 Agent（钩子取消），再发布新 Binding。
      const newGeneration = currentGeneration + 1;
      const newWorkflowId = this.options.randomId();
      // 接管恢复执行所有权：resumeSessionId 必须用持久化原 sessionId（不是随机新 id）。
      // 确无原 sessionId 时才按新会话处理（并在 binding 上明确标注）。
      const resumeSessionId = persisted?.sessionId ?? this.options.randomId();
      const resumedExisting = persisted?.sessionId !== undefined;

      const handle = await this.options.runtime.resume({
        resumeSessionId,
        setup: (agentCtx, agent) => this.options.scopeSetup?.(agentCtx, agent),
      });
      const agent = handle.agent;

      let binding: Binding;
      try {
        binding = await this.publishBinding({
          runId: input.runId,
          workflowId: newWorkflowId,
          generation: newGeneration,
          agentId: agent.id,
          sessionId: resumeSessionId,
          workspaceId: input.workspaceId,
          phase: 'starting',
          expectedGeneration: currentGeneration,
        });
      } catch (error) {
        await handle.dispose().catch(() => undefined);
        if (error instanceof Error && error.message === 'VERSION_CONFLICT') {
          return businessFailure('VERSION_CONFLICT', '接管代次冲突，请刷新后重试');
        }
        return businessFailure('INTERNAL', '接管绑定发布失败');
      }

      // 发布成功后撤销旧绑定：停旧 Agent + 撤销其 intent（同一可恢复事务已在 facade 完成 CAS）。
      if (record) {
        this.revokeRecord(record, `被 generation ${newGeneration} 接管`);
      }

      this.records.set(input.runId, { binding, handle, revokedIntents: new Set() });
      // 返回是否复用了原 session（供测试与宿主观测）。
      return businessSuccess({ binding, resumedExisting });
    } catch (error) {
      this.options.onError?.(redactError(error));
      return businessFailure('INTERNAL', '恢复写作会话失败');
    }
  }

  async cancelRun(input: CancelRunInput): Promise<BusinessResult<{ runId: string; phase: 'cancelling' | 'cancelled'; generation: number }>> {
    const record = this.records.get(input.runId);
    if (!record) {
      return businessFailure('NOT_FOUND', '该写作任务没有活跃会话');
    }
    if (record.binding.generation !== input.generation) {
      return businessFailure('VERSION_CONFLICT', '接管代次冲突，请刷新后重试');
    }
    try {
      record.handle.agent.cancel({ kind: 'user' });
      await record.handle.dispose().catch(() => undefined);
    } catch (error) {
      this.options.onError?.(redactError(error));
    }
    // 返修二轮-3：取消路径撤销失败不得吞掉——撤销失败返回失败并保留活跃记录，不能报 cancelled。
    const revokeResp = await this.options.bridge('binding.revoke-intents', { runId: input.runId });
    if (revokeResp.ok === false) {
      return businessFailure('INTERNAL', '撤销未消费推送意图失败，任务仍处于取消中');
    }
    this.records.delete(input.runId);
    return businessSuccess({ runId: input.runId, phase: 'cancelled', generation: input.generation });
  }

  /** 停止并撤销旧工作流（不复活旧租约）。 */
  private revokeRecord(record: WorkflowBindingRecord, reason: string): void {
    try {
      record.handle.agent.cancel({ kind: 'hook', reason });
    } catch {
      // 忽略：Agent 可能已退出。
    }
    void record.handle.dispose().catch(() => undefined);
  }

  private async persistedGeneration(runId: string): Promise<number | null> {
    const binding = await this.persistedBinding(runId);
    return binding?.generation ?? null;
  }

  /** 从 facade 持久化 binding 读取完整绑定（含 sessionId，重启恢复用）。 */
  private async persistedBinding(runId: string): Promise<Binding | null> {
    const resp = await this.options.bridge('binding.get', { runId });
    if (resp.ok === false) return null;
    const data = resp.data as { binding?: Binding | null };
    return data.binding ?? null;
  }

  /** 资源卸载：全部 Agent 按 disposed 语义回收（最终 handle.dispose）。 */
  async dispose(): Promise<void> {
    for (const record of [...this.records.values()]) {
      try {
        record.handle.agent.cancel({ kind: 'disposed' });
      } catch {
        // 忽略。
      }
      await record.handle.dispose().catch(() => undefined);
    }
    this.records.clear();
  }

  private followupText(input: StartRunInput, binding: Binding): string {
    return [
      `请开始 WeWrite 写作任务。`,
      `runId: ${binding.runId}`,
      `workflowId: ${binding.workflowId}`,
      `generation: ${binding.generation}`,
      `mode: ${input.mode}`,
      `visualMode: ${input.visualMode}`,
      `themeId: ${input.themeId}`,
      `topic: ${input.topic}`,
      `数据根: ${this.options.homeLabel}`,
      `主 skill: ${this.options.mainSkillName}`,
      `请按主 skill 的流程执行（run 已创建，不要执行 wewrite run start/resume）。`,
    ].join('\n');
  }
}

/** 登记 secret 到脱敏注册表（生产在拿到凭据时调用；S3 无微信凭据，占位）。 */
export function registerCredentialSecrets(secrets: readonly string[]): void {
  addSecrets(secrets);
}

export type { AgentCancelCause };
