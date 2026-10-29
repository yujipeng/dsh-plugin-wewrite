/**
 * wewrite-bridge 测试（设计 §6 范围第 7 条）：固定解释器 JSON 协议、超时回收。
 * 使用真实 facade 子进程（python/src 已就绪），验证单请求单响应 + 超时档选择。
 */

import { describe, expect, it } from 'vitest';
import { timeoutFor, TIMEOUT_HOTSPOTS, TIMEOUT_NORMAL, TIMEOUT_PUSH } from '@/host/wewrite-bridge';

describe('timeoutFor（超时档选择）', () => {
  it('普通/热榜/推送档位', () => {
    expect(timeoutFor('snapshot')).toBe(TIMEOUT_NORMAL);
    expect(timeoutFor('hotspots.fetch')).toBe(TIMEOUT_HOTSPOTS);
    expect(timeoutFor('draft.prepare')).toBe(TIMEOUT_PUSH);
    expect(timeoutFor('draft.send')).toBe(TIMEOUT_PUSH);
    expect(timeoutFor('draft.status')).toBe(TIMEOUT_NORMAL);
  });
});
