/**
 * JEE-1956 缺口 5：生产路由分派入口证据。
 *
 * 不走「保存的 handler 引用」直调——而是用生产 createHandler(WorkbenchService) 经
 * registerWebRpcRoute 注册为真实 prefix 路由，再以 owner 身份的 HTTP 请求穿过路由
 * handler 完成分派，验证 run/create 业务响应端到端可达（facade 真实子进程）。
 * 证据环境不可用时显式失败（不静默 skip）。
 */

import { describe, expect, it, vi } from 'vitest';
import { EventEmitter } from 'node:events';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import type { IncomingMessage, ServerResponse } from 'node:http';
import { registerWebRpcRoute, type WebServerRoute } from '@/host/web-route';
import { createHandler } from '@/host/rpc';
import { WorkbenchService } from '@/host/workbench-service';
import { AgentWorkflow } from '@/host/agent-workflow';
import { runFacade, timeoutFor, TIMEOUT_PUSH } from '@/host/wewrite-bridge';
import { requireFacade } from './facade-env';

function fakeReq(method: string, url: string, body: string): IncomingMessage & { destroy: () => void } {
  const req = new EventEmitter() as IncomingMessage & { method: string; url: string; headers: Record<string, string>; destroy: () => void };
  req.method = method;
  req.url = url;
  req.headers = { 'content-type': 'application/json' };
  req.destroy = vi.fn();
  queueMicrotask(() => {
    req.emit('data', Buffer.from(body));
    req.emit('end');
  });
  return req;
}

function fakeRes(): ServerResponse & { writeHead: ReturnType<typeof vi.fn>; end: ReturnType<typeof vi.fn>; on: ReturnType<typeof vi.fn>; writableEnded: boolean } {
  return {
    writeHead: vi.fn(),
    end: vi.fn(),
    on: vi.fn(),
    writableEnded: false,
  } as never;
}

describe('生产路由分派入口（JEE-1956 缺口 5）', () => {
  it('run/create 经 registerWebRpcRoute 注册的真实路由分派到 facade', async () => {
    const interpreter = await requireFacade();
    const home = mkdtempSync(join(tmpdir(), 'wewrite-route-'));
    const bridge = async (operation: string, payload: Record<string, unknown>) => {
      const result = await runFacade(
        { operation, payload, requestId: (payload.requestId as string | undefined) ?? 'r' },
        { interpreter, home },
      );
      if (result.issue) return { ok: false, error: { code: result.issue.code, message: result.issue.message, retryable: false } };
      if (result.timedOut) return { ok: false, error: { code: 'TIMEOUT', message: '操作超时', retryable: true } };
      if (!result.response) return { ok: false, error: { code: 'INTERNAL', message: '内部错误', retryable: false } };
      return result.response.ok ? { ok: true, data: result.response.data } : { ok: false, error: result.response.error };
    };
    const workflow = new AgentWorkflow({
      runtime: {
        create: async () => { throw new Error('agents 缺失'); },
        resume: async () => { throw new Error('agents 缺失'); },
        get: () => undefined,
      },
      bridge,
      homeLabel: '数据根',
      mainSkillName: 'wewrite',
      randomId: () => 'id',
    });
    const service = new WorkbenchService({ bridge, workflow, randomId: () => 'id' });

    // 生产装配：createHandler → registerWebRpcRoute（真实注册路由），非保存 handler 直调。
    const handler = createHandler(service);
    let captured!: WebServerRoute;
    registerWebRpcRoute(
      { register: (r: WebServerRoute) => { captured = r; return () => undefined; } },
      { admit: () => ({ peer: { principal: { kind: 'owner' } } }) },
      '/wewrite-python',
      handler,
    );

    const envelope = {
      type: 'client-request',
      rpcId: 'route-r1',
      method: 'run/create',
      payload: {
        requestId: 'route-r1',
        topic: '路由证据',
        mode: 'draft',
        visualMode: 'none',
        themeId: 'professional-clean',
      },
    };
    const req = fakeReq('POST', '/wewrite-python/run/create', JSON.stringify(envelope));
    const res = fakeRes();
    await captured.handler(req, res);

    const written = res.end.mock.calls[0][0] as string;
    const parsed = JSON.parse(written);
    expect(parsed.type).toBe('server-response');
    expect(parsed.rpcId).toBe('route-r1');
    expect(parsed.result.ok).toBe(true);
    expect(parsed.result.value.ok).toBe(true);
    expect(parsed.result.value.data.phase).toBe('created');
    expect(parsed.result.value.data.runId).toMatch(/^[0-9]{8}-[0-9]{6}-[a-f0-9]{6}$/);
  });

  it('timeoutFor draft.send 走推送档（路由面依赖）', () => {
    expect(timeoutFor('draft.send')).toBe(TIMEOUT_PUSH);
  });
});
