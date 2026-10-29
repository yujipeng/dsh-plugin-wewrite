/**
 * Agent 工具测试（设计 §5 两条路径 + 工具白名单）。
 * - 继承层 restrict 只过滤继承层（本插件不擅自枚举宿主 preset，部署配置 allowlist）。
 * - 自身层与 reserved run_code 由单调 guard 拒绝，副作用为零。
 */

import { describe, expect, it } from 'vitest';
import { AGENT_TOOL_NAMES, CONTROLLED_TOOL_SET, RUN_CODE_NAME, buildAgentTools } from '@/host/tools';
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

  it('guard 白名单：受控工具放行，reserved run_code 与任意工具拒绝', () => {
    for (const name of AGENT_TOOL_NAMES) {
      expect(CONTROLLED_TOOL_SET.has(name)).toBe(true);
    }
    expect(CONTROLLED_TOOL_SET.has(RUN_CODE_NAME)).toBe(false);
    expect(CONTROLLED_TOOL_SET.has('bash')).toBe(false);
    expect(CONTROLLED_TOOL_SET.has('wewrite_push_draft')).toBe(false);
  });
});
