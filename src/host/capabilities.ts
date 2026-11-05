/**
 * 能力探测与诊断快照（§1 范围第 4 条 + §3：能力探测在缺依赖时给明确诊断，不是静默失败）。
 * 逐项探测：解释器路径/版本、数据根（WEWRITE_HOME）可写性、RPC/skills/Agent/审批/生命周期。
 * 全部通过 ctx.get 无副作用读取（不声明 inject、不阻塞），服务未就绪即给诊断。
 */

import { execFileSync } from 'node:child_process';
import { accessSync, constants, existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import type { HostContext } from './types';
import type { DiagnosticAction, SnapshotDiagnostic } from '../shared/contract';

export interface CapabilityItem {
  readonly key: string;
  readonly label: string;
  readonly ok: boolean;
  readonly detail: string;
  /** 缺依赖时的可执行动作（install/configure/retry），仅呈现、不代执行。 */
  readonly actions?: readonly DiagnosticAction[];
}

export interface CapabilitySnapshot {
  readonly read: boolean;
  readonly write: boolean;
  readonly agent: boolean;
  readonly skills: boolean;
  readonly preview: boolean;
  readonly push: boolean;
  readonly items: readonly CapabilityItem[];
  readonly diagnostics: readonly SnapshotDiagnostic[];
}

/** 缺依赖时引导脚本的固定命令（脚本随包分发，路径运行时解析，不含机器绝对路径）。 */
export function installCommand(): string {
  return `python3 ${installScriptPath()}`;
}

/**
 * 解析随包引导脚本路径。开发态（vitest/tsx）本文件在 src/host/，发布态被打进 lib/index.js，
 * 两种情况下包根的相对层级不同；这里按磁盘真实存在性选一个，保证两端都命中 <包根>/scripts。
 */
export function installScriptPath(): string {
  const candidates = [
    fileURLToPath(new URL('../../scripts/install_deps.py', import.meta.url)),
    fileURLToPath(new URL('../scripts/install_deps.py', import.meta.url)),
  ];
  for (const candidate of candidates) {
    if (existsSync(candidate)) return candidate;
  }
  // 兜底：都不存在（如纯单元测试无源码树）时返回相对形态，仍不含机器绝对路径。
  return '<包根>/scripts/install_deps.py';
}

/** 解释器缺失/不可用时给「一条命令装齐」的引导。 */
function interpreterInstallAction(): DiagnosticAction {
  return {
    label: '一条命令装齐 Python 依赖',
    command: installCommand(),
    kind: 'install',
    recheck: true,
  };
}

/** 解释器探测：目标 Python 解释器路径（可被 profile 私有覆盖配置替换）。 */
function pythonInterpreter(): string {
  return process.env.WEWRITE_PYTHON ?? 'python3';
}

function probeInterpreter(): CapabilityItem {
  const interpreter = pythonInterpreter();
  try {
    const version = execFileSync(interpreter, ['--version'], { encoding: 'utf8', timeout: 5000 }).trim();
    return { key: 'interpreter', label: 'Python 解释器', ok: true, detail: `${interpreter}（${version}）` };
  } catch (error) {
    return {
      key: 'interpreter',
      label: 'Python 解释器',
      ok: false,
      detail: `解释器 ${interpreter} 不可用（${error instanceof Error ? error.message : String(error)}）`,
      actions: [interpreterInstallAction()],
    };
  }
}

/** 数据根（WEWRITE_HOME）可写性探测。 */
function probeDataRoot(): CapabilityItem {
  const home = process.env.WEWRITE_HOME;
  if (!home) {
    return {
      key: 'dataRoot',
      label: '数据根（WEWRITE_HOME）',
      ok: false,
      detail: '未配置 WEWRITE_HOME，无法确定数据根（profile 私有覆盖配置须提供）',
      actions: [interpreterInstallAction()],
    };
  }
  try {
    accessSync(home, constants.R_OK | constants.W_OK);
    return { key: 'dataRoot', label: '数据根（WEWRITE_HOME）', ok: true, detail: `${home} 可读写` };
  } catch (error) {
    return {
      key: 'dataRoot',
      label: '数据根（WEWRITE_HOME）',
      ok: false,
      detail: `${home} 不可读写（${error instanceof Error ? error.message : String(error)}）`,
      actions: [interpreterInstallAction()],
    };
  }
}

/** 逐项探测：任何宿主服务都可缺失，逐项记录而不是整体抛错。 */
export function detectCapabilities(ctx: HostContext, config: { pushEnabled: boolean }): CapabilitySnapshot {
  const items: CapabilityItem[] = [];
  const get = ctx.get?.bind(ctx) ?? (() => undefined);

  items.push(probeInterpreter());
  items.push(probeDataRoot());

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

  const diagnostics: SnapshotDiagnostic[] = items
    .filter((item) => !item.ok && item.key !== 'approval')
    .map((item) => ({
      code: 'CAPABILITY_UNAVAILABLE',
      message: item.detail,
      blocking: item.key !== 'rpc',
      ...(item.actions && item.actions.length > 0 ? { actions: [...item.actions] } : {}),
    }));

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
