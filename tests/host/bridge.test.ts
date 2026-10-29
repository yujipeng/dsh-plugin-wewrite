/**
 * wewrite-bridge 测试（设计 §6 范围第 7 条）：固定解释器 JSON 协议、超时回收、child close 回调。
 * 使用真实 facade 子进程（python/src 已就绪），验证单请求单响应 + 超时档选择。
 * 证据环境不可用时显式失败（不静默 skip）。
 */

import { describe, expect, it } from 'vitest';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { timeoutFor, runFacade, shouldRecoverAfterClose, TIMEOUT_HOTSPOTS, TIMEOUT_NORMAL, TIMEOUT_PUSH } from '@/host/wewrite-bridge';
import { requireFacade } from './facade-env';

describe('timeoutFor（超时档选择）', () => {
  it('普通/热榜/推送档位', () => {
    expect(timeoutFor('snapshot')).toBe(TIMEOUT_NORMAL);
    expect(timeoutFor('hotspots.fetch')).toBe(TIMEOUT_HOTSPOTS);
    expect(timeoutFor('draft.prepare')).toBe(TIMEOUT_PUSH);
    expect(timeoutFor('draft.send')).toBe(TIMEOUT_PUSH);
    expect(timeoutFor('draft.status')).toBe(TIMEOUT_NORMAL);
  });
});

describe('runFacade onChildClose（JEE-1956 缺口 3）', () => {
  it('shouldRecoverAfterClose：仅 draft.send 且未成功/超时才触发', () => {
    const runId = '20261029-120000-abcdef';
    // draft.send 成功且未超时 → 不恢复。
    expect(shouldRecoverAfterClose({ operation: 'draft.send', runId, ok: true, timedOut: false })).toBe(false);
    // draft.send 业务失败 → 恢复。
    expect(shouldRecoverAfterClose({ operation: 'draft.send', runId, ok: false, timedOut: false })).toBe(true);
    // draft.send 超时 → 恢复。
    expect(shouldRecoverAfterClose({ operation: 'draft.send', runId, ok: false, timedOut: true })).toBe(true);
    // 非 draft.send 操作 → 不恢复。
    expect(shouldRecoverAfterClose({ operation: 'draft.prepare', runId, ok: false, timedOut: false })).toBe(false);
    // 缺 runId → 不恢复。
    expect(shouldRecoverAfterClose({ operation: 'draft.send', ok: false, timedOut: false })).toBe(false);
  });

  it('child close 前 0 次、close 后恰 1 次回调', async () => {
    const interpreter = await requireFacade();
    const home = mkdtempSync(join(tmpdir(), 'wewrite-close-'));
    let calls = 0;

    const result = await runFacade(
      { operation: 'run.detail', requestId: 'r-close', payload: { runId: '20261029-120000-abcdef' } },
      {
        interpreter,
        home,
        onChildClose: ({ ok }) => {
          calls += 1;
          expect(ok).toBe(false); // run.detail 对不存在 run 返回业务失败
        },
      },
    );

    // run.detail 对不存在 run 返回业务失败，但子进程正常退出（ok:false，非 timedOut）。
    expect(result.response?.ok).toBe(false);
    expect(result.returncode).toBe(0);
    expect(calls).toBe(1);
  });

  it('成功路径 child close 后恰 1 次回调且 ok=true', async () => {
    const interpreter = await requireFacade();
    const home = mkdtempSync(join(tmpdir(), 'wewrite-close-ok-'));
    let calls = 0;
    const created = await runFacade(
      {
        operation: 'run.create',
        requestId: 'r-create',
        payload: { requestId: 'r-create', topic: 't', mode: 'draft', visualMode: 'none', themeId: 'professional-clean' },
      },
      {
        interpreter,
        home,
        onChildClose: ({ ok }) => {
          calls += 1;
          expect(ok).toBe(true);
        },
      },
    );
    expect(created.response?.ok).toBe(true);
    expect(calls).toBe(1);
  });
});
