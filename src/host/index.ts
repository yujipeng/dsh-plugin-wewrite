/**
 * dsh-plugin-wewrite 宿主插件入口（S1：工程骨架与契约层，只装配零业务）。
 * - apply(ctx) 生命周期：能力探测（ctx.get 无副作用）→ 子 fiber 注册 RPC/skills → 全部 disposer 登记。
 * - 依赖按本机 0.2.1-rc.4 编译；服务缺失时逐项诊断（§9.1），不是静默失败。
 * - rc.4 实测：connection.rpc.handle 内部会 owner.webServer.register，因此 RPC 注册必须
 *   走 ctx.inject(['connection','webServer'], ...) 子 fiber（与 dsh-api-gateway 同款装配）。
 */

import { z } from 'zod';
import { detectCapabilities, type CapabilitySnapshot } from './capabilities';
import { redactError, redactKeys } from './redaction';
import { createHandler, type SkeletonService } from './rpc';
import { RPC_CHANNEL } from '../shared/contract';
import { registerWebRpcRoute } from './web-route';
import { resolveLogger, type Disposer, type HostContext } from './types';

export const name = 'dsh-plugin-wewrite';

/** 顶层不阻塞等待任何可选服务；具体能力在子 fiber 内装配。 */
export const inject: readonly string[] = [];

export const Config = z.strictObject({
  agentToolsEnabled: z.boolean().default(true),
  pushEnabled: z.boolean().default(false),
  publishTransport: z.enum(['fake', 'wechat']).default('fake'),
  bridgeTimeoutMs: z.number().int().min(1000).default(30000),
});

export type PluginConfig = z.infer<typeof Config>;

/** 骨架期最小服务：只提供 snapshot 诊断版（业务 RPC 属后续 stage）。 */
class SkeletonSnapshotService implements SkeletonService {
  constructor(
    private readonly capabilities: () => CapabilitySnapshot,
    private readonly config: PluginConfig,
  ) {}

  async snapshot(): Promise<unknown> {
    const caps = this.capabilities();
    return {
      runs: [],
      nextCursor: null,
      themes: [],
      capabilities: {
        read: caps.read,
        write: caps.write,
        agent: caps.agent,
        skills: caps.skills,
        preview: caps.preview,
        push: caps.push,
      },
      diagnostics: caps.diagnostics,
      settings: {
        visualMode: 'none',
        pushEnabled: this.config.pushEnabled,
        publishTransport: this.config.publishTransport,
        credentialConfigured: false,
      },
    };
  }
}

function servicesOf(ctx: HostContext) {
  const get = ctx.get?.bind(ctx) ?? (() => undefined);
  return {
    get,
    connection: get('connection') as { rpc?: { handle?: (...args: unknown[]) => unknown } } | undefined,
    skills: get('skills') as { registerProvider?: (create: (control: unknown) => unknown) => () => void } | undefined,
  };
}

export async function apply(ctx: HostContext, rawConfig: unknown): Promise<void> {
  const config = Config.parse(rawConfig ?? {});
  const logger = resolveLogger(ctx, 'dsh-plugin-wewrite');

  let snapshot: CapabilitySnapshot = detectCapabilities(ctx, config);
  const service = new SkeletonSnapshotService(() => snapshot, config);

  const disposers: Disposer[] = [];
  const refresh = () => {
    snapshot = detectCapabilities(ctx, config);
  };

  // RPC：必须子 fiber 内带 connection + webServer 装配（rc.4 实测约束：
  // connection.rpc.handle 内部 owner.webServer 会抛，改走 webServer.register + connection.admit）。
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
        try {
          const stopRpc = registerWebRpcRoute(
            webServer as never,
            connection as never,
            RPC_CHANNEL,
            createHandler(service),
          );
          logger.info('dsh-plugin-wewrite: RPC 通道已注册 /wewrite-python');
          if (typeof stopRpc === 'function') {
            disposers.push(() => Promise.resolve(stopRpc()).catch(() => undefined));
          }
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

  // skills provider：子 fiber 内装配（rc.4 官方模式），首版仅登记占位，不加载业务 skill。
  if (typeof ctx.inject === 'function') {
    try {
      ctx.inject(['skills'], (skillsCtx) => {
        const { skills } = servicesOf(skillsCtx);
        if (typeof skills?.registerProvider !== 'function') {
          logger.warn('dsh-plugin-wewrite: skills 服务未就绪，随包 provider 不可注册');
          return;
        }
        try {
          const stopProvider = skills.registerProvider(() => ({
            name: 'wewrite-python-bundle',
            list: async () => [],
            get: async () => undefined,
          }));
          if (typeof stopProvider === 'function') disposers.push(stopProvider);
        } catch (error) {
          logger.warn(`dsh-plugin-wewrite: skills provider 注册失败：${redactError(error)}`);
        }
        refresh();
      });
    } catch (error) {
      logger.warn(`dsh-plugin-wewrite: skills 子 fiber 装配失败：${redactError(error)}`);
    }
  }

  // 全量诊断日志：逐项、脱敏。
  for (const item of snapshot.items) {
    if (item.ok) logger.info(`dsh-plugin-wewrite: 能力探测 ${item.label}：${item.detail}`);
    else logger.warn(`dsh-plugin-wewrite: 能力探测 ${item.label} 不可用：${item.detail}`);
  }
  if (snapshot.diagnostics.length > 0) {
    logger.warn(`dsh-plugin-wewrite: 诊断快照 ${JSON.stringify(redactKeys(snapshot.diagnostics))}`);
  }

  // 生命周期：全部 disposer 登记，卸载时逆序回收。
  if (typeof ctx.effect === 'function') {
    ctx.effect(async () => {
      for (const dispose of [...disposers].reverse()) {
        await Promise.resolve(dispose()).catch(() => undefined);
      }
    }, 'dsh-plugin-wewrite: host service');
  }
}
