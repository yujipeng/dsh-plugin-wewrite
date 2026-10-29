/**
 * dsh-plugin-wewrite 宿主插件入口（S3：业务 RPC + Agent 工具 + skills 装载 + 工作台 UI）。
 * - apply(ctx)：能力探测 → 子 fiber 装配 RPC/skills/Agent 工具 → 全部 disposer 登记。
 * - RPC 与 Agent 工具是同一 WorkbenchService 的两个 Adapter（设计 §2）。
 * - rc.4 实测：RPC 必须走 ctx.inject(['connection','webServer']) 子 fiber。
 */

import { z } from 'zod';
import { detectCapabilities, type CapabilitySnapshot } from './capabilities';
import { redactError } from './redaction';
import { createHandler } from './rpc';
import { RPC_CHANNEL } from '../shared/contract';
import { registerWebRpcRoute } from './web-route';
import { resolveLogger, type Disposer, type HostContext } from './types';
import { WorkbenchService, type WorkbenchBridge } from './workbench-service';
import { AgentWorkflow, type DshAgentRuntime } from './agent-workflow';
import { runFacade } from './wewrite-bridge';
import { buildAgentTools, setToolService, controlledToolGuard, INHERITED_TOOL_ALLOWLIST } from './tools';
import { buildProvider, defaultManifestPath, PROVIDER_NAME } from './skills-provider';
import { randomUUID } from 'node:crypto';
import { installModelSelection } from '@deepseek-ai/dsh-agent';

export const name = 'dsh-plugin-wewrite';

// 供真实 Agent e2e 证据脚本复用（不进入 client 面）。
export { AgentWorkflow } from './agent-workflow';
export type { DshAgentRuntime } from './agent-workflow';
export { WorkbenchService } from './workbench-service';
export type { WorkbenchBridge } from './workbench-service';
export { runFacade } from './wewrite-bridge';
export { buildAgentTools, setToolService, controlledToolGuard, INHERITED_TOOL_ALLOWLIST } from './tools';
export { createHandler } from './rpc';
export { RPC_CHANNEL } from '../shared/contract';

/** 顶层不阻塞等待任何可选服务；具体能力在子 fiber 内装配。 */
export const inject: readonly string[] = [];

export const Config = z.strictObject({
  agentToolsEnabled: z.boolean().default(true),
  pushEnabled: z.boolean().default(false),
  publishTransport: z.enum(['fake', 'wechat']).default('fake'),
  bridgeTimeoutMs: z.number().int().min(1000).default(30000),
  interpreter: z.string().optional(),
  wewriteHome: z.string().optional(),
});

export type PluginConfig = z.infer<typeof Config>;

interface AgentCtxLike {
  get?: (name: string) => unknown;
  tools?: { register: (definition: unknown) => () => void; restrict?: (filter: unknown) => () => void; guard?: (guard: unknown) => () => void };
}

/** 把真实 DSH AgentRuntime 适配为最小消费面（含生产 model selection 装配）。 */
function agentRuntimeFrom(ctx: HostContext): DshAgentRuntime | undefined {
  const agents = ctx.get?.('agents') as { create?: unknown; resume?: unknown; get?: unknown } | undefined;
  if (!agents || typeof agents.create !== 'function') return undefined;
  const agentDefaultModel = ctx.get?.('agentDefaultModel') as { currentSelection?: () => { provider: string; model: string } | undefined } | undefined;
  const defaultSelection = agentDefaultModel?.currentSelection?.() ?? { provider: 'tcreditai', model: 'deepseek-flash' };
  const wrapSetup = (setup: ((agentCtx: unknown, agent: unknown) => void | Promise<void>) | undefined) =>
    (agentCtx: unknown, agent: unknown) => {
      // 生产 model selection：与 headless-runner 一致，避免 prompt 变量缺失。
      try {
        installModelSelection(agentCtx as never, { current: defaultSelection, assembled: undefined });
      } catch {
        // 忽略：宿主无 installModelSelection 时由 preset 提供。
      }
      return setup?.(agentCtx, agent);
    };
  return {
    create(options) {
      const opts = {
        ...options,
        agentOptions: { provider: defaultSelection.provider, model: defaultSelection.model },
        setup: wrapSetup(options.setup as ((agentCtx: unknown, agent: unknown) => void | Promise<void>) | undefined),
      };
      return (agents.create as (o: unknown) => Promise<import('@deepseek-ai/dsh-agent').AgentHandle>)(opts) as Promise<import('@deepseek-ai/dsh-agent').AgentHandle>;
    },
    async resume(options) {
      if (typeof agents.resume !== 'function') throw new Error('resume 不可用');
      const opts = {
        ...options,
        agentOptions: { provider: defaultSelection.provider, model: defaultSelection.model },
        setup: wrapSetup(options.setup as ((agentCtx: unknown, agent: unknown) => void | Promise<void>) | undefined),
      };
      return (agents.resume as (o: unknown) => Promise<import('@deepseek-ai/dsh-agent').AgentHandle>)(opts) as Promise<import('@deepseek-ai/dsh-agent').AgentHandle>;
    },
    get(id: string) {
      return (agents.get as (id: string) => import('@deepseek-ai/dsh-agent').Agent | undefined)(id);
    },
  };
}

/** 组装 WorkbenchService + AgentWorkflow（每个 apply 只组装一次，多处 fiber 共享同一实例）。 */
function assembleWorkbench(
  ctx: HostContext,
  config: PluginConfig,
  logger: ReturnType<typeof resolveLogger>,
  get: (name: string) => unknown,
) {
  const interpreter = config.interpreter ?? process.env.WEWRITE_PYTHON ?? 'python3';
  const home = config.wewriteHome ?? process.env.WEWRITE_HOME ?? '';

  // facade bridge：固定解释器 + JSON 子进程协议。
  const bridge: WorkbenchBridge = async (operation, payload) => {
    const result = await runFacade(
      {
        operation,
        payload,
        requestId: (payload.requestId as string | undefined) ?? randomUUID(),
      },
      { interpreter, home, timeoutMs: config.bridgeTimeoutMs },
    );
    if (result.issue) {
      return { ok: false, error: { code: result.issue.code, message: result.issue.message, retryable: false } };
    }
    if (result.timedOut) {
      return { ok: false, error: { code: 'TIMEOUT', message: '操作超时', retryable: true } };
    }
    if (!result.response) {
      return { ok: false, error: { code: 'INTERNAL', message: '内部错误', retryable: false } };
    }
    if (result.response.ok === false) {
      return { ok: false, error: result.response.error };
    }
    return { ok: true, data: result.response.data };
  };

  const runtime = agentRuntimeFrom(ctx);

  const service = new WorkbenchService({
    bridge,
    workflow: undefined as unknown as AgentWorkflow,
    randomId: () => randomUUID(),
    replay: new Map(),
    onError: (message) => logger.warn(`dsh-plugin-wewrite: 业务错误：${message}`),
  });

  const toolDefs = buildAgentTools(service);

  const workflow = new AgentWorkflow({
    runtime: runtime ?? {
      create: async () => { throw new Error('agents 服务缺失'); },
      resume: async () => { throw new Error('agents 服务缺失'); },
      get: () => undefined,
    },
    bridge,
    scopeSetup: (agentCtx) => {
      // 专属写作 Agent 的 scope 装配：注册 8 个受控工具 + 继承层 restrict + 单调 guard。
      // 设计 §5 两条路径分别落实：
      // 1) 继承层：restrict({allow: 空清单}) 只过滤继承的全局/祖先层工具（shell/通用写入/代码执行/委派），
      //    不把 scope-local 名称、未知名称或 reserved `run_code` 传给 restrict。
      // 2) 自身层与 reserved：guard 显式拒绝 run_code/自身层危险工具，native 插件工具仍可执行。
      if (config.agentToolsEnabled === false) return;
      const agentTools = (agentCtx as { get?: (name: string) => unknown }).get?.('tools') as
        | { register?: (d: unknown) => () => void; restrict?: (f: unknown) => () => void; guard?: (g: unknown) => () => void }
        | undefined;
      if (typeof agentTools?.register !== 'function') return;
      for (const def of toolDefs) {
        try {
          agentTools.register(def);
        } catch (error) {
          logger.warn(`dsh-plugin-wewrite: 工具 ${def.name} scope 注册失败：${redactError(error)}`);
        }
      }
      if (typeof agentTools.restrict === 'function') {
        try {
          agentTools.restrict({ allow: [...INHERITED_TOOL_ALLOWLIST] });
        } catch (error) {
          logger.warn(`dsh-plugin-wewrite: tools.restrict 继承层过滤失败：${redactError(error)}`);
        }
      }
      if (typeof agentTools.guard === 'function') {
        try {
          agentTools.guard(controlledToolGuard);
        } catch (error) {
          logger.warn(`dsh-plugin-wewrite: tools.guard scope 注册失败：${redactError(error)}`);
        }
      }
    },
    homeLabel: home ? '数据根（已配置，不显示路径）' : '数据根未配置',
    mainSkillName: 'wewrite',
    randomId: () => randomUUID(),
    onError: (message) => logger.warn(`dsh-plugin-wewrite: Agent 工作流错误：${message}`),
  });

  (service as unknown as { options: { workflow: AgentWorkflow } }).options.workflow = workflow;

  setToolService(service);

  return { service, workflow, bridge, interpreter, home, runtime };
}

/** 生产装配结果注册表（供真实 profile 装载后的取证/宿主探针读取，不进入 client 面）。 */
export interface AssembledWorkbench {
  readonly service: WorkbenchService;
  readonly workflow: AgentWorkflow;
  readonly bridge: WorkbenchBridge;
  readonly interpreter: string;
  readonly home: string;
  /** 实际注册到 RPC 路由的 handler（七轮-5 取证用）。 */
  readonly handler?: ReturnType<typeof createHandler>;
}

const assembledByProfile = new Map<string, AssembledWorkbench>();

/** 由 apply 写入的生产装配结果（真实 profile 装载时记录）。 */
export function getAssembled(profile: string): AssembledWorkbench | undefined {
  return assembledByProfile.get(profile);
}

export async function apply(ctx: HostContext, rawConfig: unknown): Promise<void> {
  const config = Config.parse(rawConfig ?? {});
  const logger = resolveLogger(ctx, 'dsh-plugin-wewrite');

  let snapshot: CapabilitySnapshot = detectCapabilities(ctx, config);
  const disposers: Disposer[] = [];
  const refresh = () => {
    snapshot = detectCapabilities(ctx, config);
  };

  // ── 生产装配（只组装一次，多个 fiber 共享同一 service/workflow 实例）──────────────
  // assembleWorkbench 需要 agents 服务；在 ['agents','skills'] 子 fiber 组装一次并缓存。
  // RPC 路由和 Agent 工具都用这一个实例，避免「两套 serviceRef」导致绑定/工具错位。
  let assembledCache: ReturnType<typeof assembleWorkbench> | undefined;

  const getAssembledOnce = (fiberCtx: HostContext, get: (name: string) => unknown) => {
    if (!assembledCache) {
      assembledCache = assembleWorkbench(fiberCtx, config, logger, get);
      const record: AssembledWorkbench = {
        service: assembledCache.service,
        workflow: assembledCache.workflow,
        bridge: assembledCache.bridge,
        interpreter: assembledCache.interpreter,
        home: assembledCache.home,
      };
      const profileCtx = get('profileContext') as { name?: string } | undefined;
      const profileName = profileCtx?.name ?? 'default';
      assembledByProfile.set('default', record);
      assembledByProfile.set(profileName, record);
      (globalThis as unknown as { __wewriteAssembled?: AssembledWorkbench }).__wewriteAssembled = record;
    }
    return assembledCache;
  };

  // ── skills provider：子 fiber 内装配（rc.4 官方模式）──────────────
  if (typeof ctx.inject === 'function') {
    try {
      ctx.inject(['skills'], (skillsCtx) => {
        const get = skillsCtx.get?.bind(skillsCtx) ?? (() => undefined);
        const skills = get('skills') as { registerProvider?: (create: (control: unknown) => unknown) => () => void } | undefined;
        if (typeof skills?.registerProvider === 'function') {
          try {
            const stopProvider = skills.registerProvider(() => buildProvider({ manifestPath: defaultManifestPath() }));
            if (typeof stopProvider === 'function') disposers.push(stopProvider);
            logger.info(`dsh-plugin-wewrite: skills provider 已注册（${PROVIDER_NAME}）`);
          } catch (error) {
            logger.warn(`dsh-plugin-wewrite: skills provider 注册失败：${redactError(error)}`);
          }
        } else {
          logger.warn('dsh-plugin-wewrite: skills 服务未就绪，随包 provider 不可注册');
        }
        refresh();
      });
    } catch (error) {
      logger.warn(`dsh-plugin-wewrite: skills 子 fiber 装配失败：${redactError(error)}`);
    }
  }

  // ── Agent 工具 + 生产装配：在 ['agents'] 子 fiber 组装一次（工具随 Agent setup 注册）。──────────────
  if (typeof ctx.inject === 'function') {
    try {
      ctx.inject(['agents'], (agentsCtx) => {
        const get = agentsCtx.get?.bind(agentsCtx) ?? (() => undefined);
        getAssembledOnce(agentsCtx, get);
        refresh();
      });
    } catch (error) {
      logger.warn(`dsh-plugin-wewrite: Agent 工具子 fiber 装配失败：${redactError(error)}`);
    }
  }

  // ── RPC：子 fiber 内装配（需要 connection + webServer）──────────────
  if (typeof ctx.inject === 'function') {
    try {
      ctx.inject(['connection', 'webServer'], (rpcCtx) => {
        const get = rpcCtx.get?.bind(rpcCtx) ?? (() => undefined);
        const connection = get('connection') as { admit?: unknown } | undefined;
        const webServer = get('webServer') as { register?: unknown } | undefined;
        if (typeof connection?.admit !== 'function' || typeof webServer?.register !== 'function') {
          logger.warn('dsh-plugin-wewrite: connection/webServer 未就绪，Web 面板不可用（诊断已入快照）');
          return;
        }

        const assembled = getAssembledOnce(rpcCtx, get);

        try {
          // 七轮-5：记录实际注册到路由的 handler（e2e 走这一个，不再重新 createHandler）。
          const registeredHandler = createHandler(assembled.service);
          const record = assembledByProfile.get('default');
          if (record) {
            assembledByProfile.set('default', { ...record, handler: registeredHandler });
            (globalThis as unknown as { __wewriteAssembled?: AssembledWorkbench }).__wewriteAssembled =
              { ...record, handler: registeredHandler };
          }
          const stopRpc = registerWebRpcRoute(
            webServer as never,
            connection as never,
            RPC_CHANNEL,
            registeredHandler,
          );
          logger.info('dsh-plugin-wewrite: RPC 通道已注册 /wewrite-python');
          if (typeof stopRpc === 'function') disposers.push(() => Promise.resolve(stopRpc()).catch(() => undefined));
        } catch (error) {
          logger.warn(`dsh-plugin-wewrite: RPC 注册失败：${redactError(error)}`);
        }

        refresh();
      });
    } catch (error) {
      logger.warn(`dsh-plugin-wewrite: RPC 子 fiber 装配失败：${redactError(error)}`);
    }
  } else {
    logger.warn('dsh-plugin-wewrite: ctx.inject 缺失，RPC 无法装配（诊断已入快照）');
  }

  // ── 全量诊断日志（逐项、脱敏）──────────────
  for (const item of snapshot.items) {
    if (item.ok) logger.info(`dsh-plugin-wewrite: 能力探测 ${item.label}：${item.detail}`);
    else logger.warn(`dsh-plugin-wewrite: 能力探测 ${item.label} 不可用：${item.detail}`);
  }

  // ── 生命周期：body 只做登记、返回 disposer ──────────────
  if (typeof ctx.effect === 'function') {
    ctx.effect(
      () => async () => {
        for (const dispose of [...disposers].reverse()) {
          await Promise.resolve(dispose()).catch(() => undefined);
        }
      },
      'dsh-plugin-wewrite: host service',
    );
  }
}
