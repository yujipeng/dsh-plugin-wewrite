/**
 * 返修二轮-4：client 工作区来源 + 服务未就绪走加载态。
 * - 多工作区时按当前选中 sessionId 推导工作区（不取 items[0]）。
 * - 服务未注入时 ready=false（加载态），不是错误态。
 */

import { describe, expect, it } from 'vitest';
import { resolveWorkspaceIdBySession } from '@/client/index';
import type { ClientContext } from '@/client/context';

function makeCtx(workspaces?: ClientContext['workspaces']): ClientContext {
  return { workspaces };
}

describe('resolveWorkspaceIdBySession（工作区来源）', () => {
  it('多工作区时返回当前 session 所属工作区，不是 items[0]', () => {
    const ctx = makeCtx({
      list: {
        getSnapshot: () => ({
          items: [
            { workspaceId: 'ws-A', path: '/a', title: 'A', sessionIds: ['s-A'] },
            { workspaceId: 'ws-B', path: '/b', title: 'B', sessionIds: ['s-B'] },
          ],
        }),
      },
    });
    expect(resolveWorkspaceIdBySession(ctx, 's-B')).toBe('ws-B');
    expect(resolveWorkspaceIdBySession(ctx, 's-A')).toBe('ws-A');
  });

  it('无当前 session → null', () => {
    const ctx = makeCtx({
      list: { getSnapshot: () => ({ items: [{ workspaceId: 'ws-A', path: '/a', title: 'A', sessionIds: ['s-A'] }] }) },
    });
    expect(resolveWorkspaceIdBySession(ctx, null)).toBeNull();
  });

  it('workspaces 服务缺失 → null（由 ready=false 呈现加载态）', () => {
    const ctx = makeCtx(undefined);
    expect(resolveWorkspaceIdBySession(ctx, 's-A')).toBeNull();
  });

  it('session 不属于任何工作区 → null', () => {
    const ctx = makeCtx({
      list: { getSnapshot: () => ({ items: [{ workspaceId: 'ws-A', path: '/a', title: 'A', sessionIds: ['s-A'] }] }) },
    });
    expect(resolveWorkspaceIdBySession(ctx, 's-unknown')).toBeNull();
  });
});
