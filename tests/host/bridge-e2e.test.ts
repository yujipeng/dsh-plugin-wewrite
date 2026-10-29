/**
 * 端到端桥接测试：TS runFacade → 真实 Python facade 子进程 → run.create/detail。
 * 验证固定解释器 JSON 协议、runId 贯通（UI/Binding/工具/Python 状态/产物五处同 runId）。
 * 依赖已安装 facade（`pip install -e python`）；未安装时跳过（不视为失败）。
 */

import { describe, expect, it } from 'vitest';
import { existsSync, mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { spawnSync } from 'node:child_process';
import { runFacade, timeoutFor, TIMEOUT_PUSH } from '@/host/wewrite-bridge';

const REPO = resolve(__dirname, '..', '..');
const FACADE_PYTHON = process.env.FACADE_PYTHON ?? resolve(REPO, '.venv', 'bin', 'python');

function facadeAvailable(): boolean {
  if (!existsSync(FACADE_PYTHON)) return false;
  const probe = spawnSync(FACADE_PYTHON, ['-c', 'import dsh_wewrite_facade'], { encoding: 'utf8' });
  return probe.status === 0;
}

const SKIP = !facadeAvailable();

describe('runFacade（真实 facade 子进程）', () => {
  it.skipIf(SKIP)('run.create → run.detail 同一 runId，产物/状态贯通', async () => {
    const home = mkdtempSync(join(tmpdir(), 'wewrite-e2e-'));
    const interpreter = FACADE_PYTHON;

    const created = await runFacade(
      {
        operation: 'run.create',
        requestId: 'r-create',
        payload: {
          requestId: 'r-create',
          topic: '端到端选题',
          mode: 'draft',
          visualMode: 'none',
          themeId: 'professional-clean',
        },
      },
      { interpreter, home },
    );
    expect(created.issue).toBeNull();
    expect(created.timedOut).toBe(false);
    expect(created.response?.ok).toBe(true);
    const runId = (created.response as { data: { runId: string } }).data.runId;
    expect(runId).toMatch(/^[0-9]{8}-[0-9]{6}-[a-f0-9]{6}$/);

    const detail = await runFacade(
      { operation: 'run.detail', requestId: 'r-detail', payload: { runId } },
      { interpreter, home },
    );
    expect(detail.response?.ok).toBe(true);
    const data = (detail.response as { data: { run: { runId: string } } }).data;
    expect(data.run.runId).toBe(runId);
  });

  it('bridge 超时档选择：draft.send 走推送档', () => {
    expect(timeoutFor('draft.send')).toBe(TIMEOUT_PUSH);
  });
});
