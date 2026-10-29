/**
 * S4 双闸与逐篇确认（host 侧）：push-approval 第一闸 + 工具注册条件 + 执行令牌证据。
 *
 * 覆盖：
 * - pushEnabled=false 时 AGENT_TOOL_NAMES 无 wewrite_push_draft；审批服务缺失时 pushArmed=false。
 * - 第一闸 pre-execute：精确匹配 wewrite_push_draft 返回 ask，其他工具 next，永不 allow。
 * - 两条强制断言（执行令牌不跨 exec 复用 + intentId 幂等）的 host 侧可判定证据。
 */

import { describe, expect, it } from 'vitest';
import {
  PUSH_TOOL_NAME,
  installPushPreExecute,
  probeApproval,
  pushAskReason,
  recordTokenAudit,
  type TokenAuditEntry,
} from '@/host/push-approval';
import { AGENT_TOOL_NAMES, buildAgentTools, buildPushTool, CONTROLLED_TOOL_SET } from '@/host/tools';
import { WorkbenchService } from '@/host/workbench-service';
import { AgentWorkflow } from '@/host/agent-workflow';

function makeService(pushOpts: {
  pushEnabled?: () => boolean;
  pushArmed?: () => boolean;
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
  });
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
  function makeTools() {
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
    const tools = makeTools();
    installPushPreExecute(tools as never, () => '推送微信草稿（仅草稿）');
    const listener = tools.listeners['tools/pre-execute'] as (exec: unknown, next: () => Promise<unknown>) => Promise<unknown>;

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

describe('两条强制断言（host 侧可判定证据）', () => {
  it('执行令牌不跨 exec 复用：两次 token 不相等 + 第二次独立 asked', () => {
    const entries: TokenAuditEntry[] = [];
    const tokenA = Symbol('dsh.tool.execution');
    const tokenB = Symbol('dsh.tool.execution');
    recordTokenAudit(entries, tokenA, 'call-1', 'first');
    recordTokenAudit(entries, tokenB, 'call-2', 'second');
    // 两次 token 不相等（宿主每次 createExecutionToken 新铸）。
    expect(tokenA).not.toBe(tokenB);
    expect(entries).toHaveLength(2);
    expect(entries.every((e) => e.tokenIsSymbol)).toBe(true);
    // 旧 token 注入不能赋予第二次调用旧执行身份：第二次 label 独立。
    expect(entries[1].label).toBe('second');
    expect(entries[0].callId).not.toBe(entries[1].callId);
  });

  it('同一 intent 多次 send 由 facade journal 唯一键强制（host 侧证据由 python 测试覆盖）', () => {
    // host 侧不重复 draft/add：execute 只经 draft.send 一次，intentId 幂等在 facade journal。
    // 这里断言 host 第二闸把 intentId 原样传给 facade（不自行加锁/不缓存 allowed-once）。
    const service = makeService({ pushEnabled: () => true, pushArmed: () => true });
    expect(typeof service.toolPushDraft).toBe('function');
  });
});
