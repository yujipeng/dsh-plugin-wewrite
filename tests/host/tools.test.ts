/**
 * Agent 工具测试（设计 §5 两条路径 + 工具白名单 + 继承层 restrict）。
 * - 路径 1：继承层危险工具 restrict({allow:[]}) 后不可见且不可执行。
 * - 路径 2：自身层注入的危险工具与 reserved run_code 被 guard 拒绝，副作用为零，native 插件工具仍可执行。
 */

import { describe, expect, it } from 'vitest';
import {
  AGENT_TOOL_NAMES,
  CONTROLLED_TOOL_SET,
  INHERITED_TOOL_ALLOWLIST,
  RUN_CODE_NAME,
  buildAgentTools,
  controlledToolGuard,
} from '@/host/tools';
import { WorkbenchService } from '@/host/workbench-service';
import { AgentWorkflow } from '@/host/agent-workflow';

function makeService() {
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

describe('Agent 工具（设计 §5 表格）', () => {
  it('8 个受控工具名与设计 §5 表格一致，且不注册 wewrite_push_draft', () => {
    expect([...AGENT_TOOL_NAMES].sort()).toEqual(
      [
        'wewrite_run_start',
        'wewrite_run_detail',
        'wewrite_artifact_get',
        'wewrite_run_step',
        'wewrite_artifact_write',
        'wewrite_review',
        'wewrite_run_finish',
        'wewrite_preview',
      ].sort(),
    );
    expect(AGENT_TOOL_NAMES).not.toContain('wewrite_push_draft');
  });

  it('工具定义可构建，名与参数 schema 具备', () => {
    const defs = buildAgentTools(makeService());
    expect(defs).toHaveLength(8);
    for (const def of defs) {
      expect(CONTROLLED_TOOL_SET.has(def.name)).toBe(true);
      expect(typeof def.execute).toBe('function');
    }
  });

  it('继承层 allowlist 为空：不把 scope-local/reserved 名传给 restrict', () => {
    // 路径 1：allow 清单只含核实过的继承只读工具；本工作流不需要任何继承层工具 → 空清单。
    expect(INHERITED_TOOL_ALLOWLIST).toEqual([]);
    // 不包含 scope-local 名与 reserved run_code。
    expect(INHERITED_TOOL_ALLOWLIST).not.toContain('wewrite_run_start');
    expect(INHERITED_TOOL_ALLOWLIST).not.toContain(RUN_CODE_NAME);
  });
});

describe('controlledToolGuard（路径 2：自身层与 reserved 拒绝）', () => {
  it('native 插件受控工具放行（返回 undefined）', () => {
    for (const name of AGENT_TOOL_NAMES) {
      expect(controlledToolGuard({ name })).toBeUndefined();
    }
  });

  it('reserved run_code 被显式拒绝', () => {
    expect(controlledToolGuard({ name: RUN_CODE_NAME })).toContain('run_code');
  });

  it('shell/通用写入/代码执行/委派等危险工具被拒绝，副作用为零', () => {
    for (const name of ['bash', 'write', 'edit', 'str_replace_editor', 'subagent', 'delegate', 'run_code']) {
      expect(controlledToolGuard({ name })).toBeTruthy();
    }
    // guard 不产生副作用：纯函数，多次调用同结果。
    expect(controlledToolGuard({ name: 'bash' })).toBe(controlledToolGuard({ name: 'bash' }));
  });

  it('未知工具被拒绝（fail-closed）', () => {
    expect(controlledToolGuard({ name: 'unknown-tool' })).toBeTruthy();
  });
});
