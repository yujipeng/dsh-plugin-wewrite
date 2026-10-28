/**
 * 能力探测与诊断快照（§1 范围第 4 条：能力探测在缺依赖时给明确诊断，不是静默失败）。
 * 逐项探测：RPC/skills/Agent/审批服务/生命周期，每项给出 ok + 中文诊断。
 * 全部通过 ctx.get 无副作用读取（不声明 inject、不阻塞），服务未就绪即给诊断。
 */

import type { HostContext } from './types';
import type { Issue } from '../shared/contract';

export interface CapabilityItem {
  readonly key: string;
  readonly label: string;
  readonly ok: boolean;
  readonly detail: string;
}

export interface CapabilitySnapshot {
  readonly read: boolean;
  readonly write: boolean;
  readonly agent: boolean;
  readonly skills: boolean;
  readonly preview: boolean;
  readonly push: boolean;
  readonly items: readonly CapabilityItem[];
  readonly diagnostics: readonly Issue[];
}

/** 逐项探测：任何宿主服务都可缺失，逐项记录而不是整体抛错。 */
export function detectCapabilities(ctx: HostContext, config: { pushEnabled: boolean }): CapabilitySnapshot {
  const items: CapabilityItem[] = [];
  const get = ctx.get?.bind(ctx) ?? (() => undefined);

  const connection = get('connection') as { rpc?: { handle?: unknown }; admit?: unknown } | undefined;
  const rpcOk = typeof connection?.admit === 'function';
  items.push({
    key: 'rpc',
    label: 'RPC 通道',
    ok: rpcOk,
    detail: rpcOk ? 'connection.admit 可用（认证可用，RPC 走 webServer 路由）' : 'connection 缺失或未就绪，Web 面板不可用',
  });

  const skills = get('skills') as { registerProvider?: unknown } | undefined;
  const skillsOk = typeof skills?.registerProvider === 'function';
  items.push({
    key: 'skills',
    label: 'Skills 注册',
    ok: skillsOk,
    detail: skillsOk ? 'skills.registerProvider 可用' : 'skills 服务缺失，随包 skills provider 不可注册',
  });

  const agents = get('agents') as { create?: unknown } | undefined;
  const agentsOk = typeof agents?.create === 'function';
  items.push({
    key: 'agent',
    label: 'Agent 创建',
    ok: agentsOk,
    detail: agentsOk ? 'agents.create 可用' : 'agents 服务缺失，无法启动专属写作 Agent',
  });

  const approval = get('approval') as { request?: unknown } | undefined;
  const approvalOk = typeof approval?.request === 'function';
  items.push({
    key: 'approval',
    label: '审批服务',
    ok: approvalOk,
    detail: approvalOk ? 'approval.request 可用' : '审批服务缺失，推送无法武装（首版默认不推送，不受阻）',
  });

  const effectOk = typeof ctx.effect === 'function';
  items.push({
    key: 'effect',
    label: '生命周期 effect',
    ok: effectOk,
    detail: effectOk ? 'ctx.effect 可用' : 'ctx.effect 缺失，无法登记 disposer',
  });

  const diagnostics: Issue[] = items
    .filter((item) => !item.ok && item.key !== 'approval')
    .map((item) => ({ code: 'CAPABILITY_UNAVAILABLE', message: item.detail, blocking: item.key !== 'rpc' }));

  return {
    read: rpcOk,
    write: false,
    agent: agentsOk,
    skills: skillsOk,
    preview: false,
    push: config.pushEnabled === true && approvalOk,
    items,
    diagnostics,
  };
}
