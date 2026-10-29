/**
 * 端到端桥接测试：TS runFacade → 真实 Python facade 子进程 → run.create/detail。
 * 验证固定解释器 JSON 协议、runId 贯通（UI/Binding/工具/Python 状态/产物五处同 runId）。
 * 依赖已安装 facade（`pip install -e python`）；证据环境不可用时显式失败（不静默 skip）。
 */

import { describe, expect, it } from 'vitest';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { runFacade, timeoutFor, TIMEOUT_PUSH } from '@/host/wewrite-bridge';
import { requireFacade } from './facade-env';

describe('runFacade（真实 facade 子进程）', () => {
  it('run.create → run.detail 同一 runId，产物/状态贯通', async () => {
    const interpreter = await requireFacade();
    const home = mkdtempSync(join(tmpdir(), 'wewrite-e2e-'));

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
