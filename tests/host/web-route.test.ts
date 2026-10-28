/**
 * Web RPC 路由测试（rc.4 适配：webServer.register + connection.admit + owner 身份边界）。
 */

import { describe, expect, it, vi } from 'vitest';
import { EventEmitter } from 'node:events';
import type { IncomingMessage, ServerResponse } from 'node:http';
import { registerWebRpcRoute, type WebServerRoute } from '@/host/web-route';
import type { RpcHandler } from '@/host/types';

function fakeReq(method: string, url: string, body?: string, headers: Record<string, string> = {}) {
  const req = new EventEmitter() as IncomingMessage & { method: string; url: string; headers: Record<string, string>; destroy: () => void };
  req.method = method;
  req.url = url;
  req.headers = { 'content-type': 'application/json', ...headers };
  req.destroy = vi.fn();
  queueMicrotask(() => {
    if (body !== undefined) req.emit('data', Buffer.from(body));
    req.emit('end');
  });
  return req;
}

function fakeRes() {
  const res = {
    writeHead: vi.fn(),
    end: vi.fn(),
    on: vi.fn(),
    writableEnded: false,
  } as unknown as ServerResponse & {
    writeHead: ReturnType<typeof vi.fn>;
    end: ReturnType<typeof vi.fn>;
    on: ReturnType<typeof vi.fn>;
  };
  return res;
}

describe('registerWebRpcRoute', () => {
  const okHandler: RpcHandler = async (endpoint, payload, _signal, _peer) => ({
    ok: true,
    value: { endpoint, payload },
  });

  const ownerAdmit = { admit: () => ({ peer: { principal: { kind: 'owner' } } }) };

  it('POST 合法请求（owner principal）→ 200 + server-response 信封', async () => {
    let captured!: WebServerRoute;
    const route = registerWebRpcRoute(
      { register: (r: WebServerRoute) => { captured = r; return () => undefined; } },
      ownerAdmit,
      '/wewrite-python',
      okHandler,
    );
    expect(typeof route).toBe('function');

    const req = fakeReq('POST', '/wewrite-python/snapshot', JSON.stringify({ type: 'client-request', rpcId: 'r-1', method: 'snapshot', payload: {} }));
    const res = fakeRes();
    await captured.handler(req, res);
    const written = res.end.mock.calls[0][0] as string;
    const parsed = JSON.parse(written);
    expect(parsed.type).toBe('server-response');
    expect(parsed.rpcId).toBe('r-1');
    expect(parsed.result.ok).toBe(true);
    expect(parsed.result.value.endpoint).toBe('snapshot');
  });

  it('非 POST 方法 → 405', async () => {
    let captured!: WebServerRoute;
    registerWebRpcRoute(
      { register: (r: WebServerRoute) => { captured = r; return () => undefined; } },
      ownerAdmit,
      '/wewrite-python',
      okHandler,
    );
    const req = fakeReq('GET', '/wewrite-python/snapshot');
    const res = fakeRes();
    await captured.handler(req, res);
    expect(res.writeHead).toHaveBeenCalledWith(405);
  });

  it('未认证（rejection 401）→ 401 unauthorized', async () => {
    let captured!: WebServerRoute;
    registerWebRpcRoute(
      { register: (r: WebServerRoute) => { captured = r; return () => undefined; } },
      { admit: () => ({ rejection: 401 }) },
      '/wewrite-python',
      okHandler,
    );
    const req = fakeReq('POST', '/wewrite-python/snapshot', JSON.stringify({ type: 'client-request', rpcId: 'r-1', method: 'snapshot', payload: {} }));
    const res = fakeRes();
    await captured.handler(req, res);
    expect(res.writeHead).toHaveBeenCalledWith(401);
    expect(res.end).toHaveBeenCalledWith('unauthorized');
  });

  it('空 peer → 403（伪造身份被拒）', async () => {
    let captured!: WebServerRoute;
    registerWebRpcRoute(
      { register: (r: WebServerRoute) => { captured = r; return () => undefined; } },
      { admit: () => ({ peer: null }) },
      '/wewrite-python',
      okHandler,
    );
    const req = fakeReq('POST', '/wewrite-python/snapshot', JSON.stringify({ type: 'client-request', rpcId: 'r-1', method: 'snapshot', payload: {} }));
    const res = fakeRes();
    await captured.handler(req, res);
    expect(res.writeHead).toHaveBeenCalledWith(403);
  });

  it('非 owner principal（远端 API）→ 403', async () => {
    let captured!: WebServerRoute;
    registerWebRpcRoute(
      { register: (r: WebServerRoute) => { captured = r; return () => undefined; } },
      { admit: () => ({ peer: { principal: { kind: 'api-operator' } } }) },
      '/wewrite-python',
      okHandler,
    );
    const req = fakeReq('POST', '/wewrite-python/snapshot', JSON.stringify({ type: 'client-request', rpcId: 'r-1', method: 'snapshot', payload: {} }));
    const res = fakeRes();
    await captured.handler(req, res);
    expect(res.writeHead).toHaveBeenCalledWith(403);
    expect(res.end).toHaveBeenCalledWith('forbidden');
  });

  it('合法 JSON null body → 400 中文信封，不抛异常', async () => {
    let captured!: WebServerRoute;
    registerWebRpcRoute(
      { register: (r: WebServerRoute) => { captured = r; return () => undefined; } },
      ownerAdmit,
      '/wewrite-python',
      okHandler,
    );
    const req = fakeReq('POST', '/wewrite-python/snapshot', 'null');
    const res = fakeRes();
    await expect(captured.handler(req, res)).resolves.toBeUndefined();
    expect(res.writeHead.mock.calls[0][0]).toBe(400);
    const written = res.end.mock.calls[0][0] as string;
    expect(JSON.parse(written).error).toContain('JSON 对象');
  });

  it('异常消息含 URL token 与 secret → 脱敏中文信封，无栈无原文', async () => {
    let captured!: WebServerRoute;
    const throwingHandler: RpcHandler = async () => {
      const err = new Error('failed https://api.example.com/x?token=SUPERSECRET');
      err.stack = 'Error: failed\n    at secret.js:1:1';
      throw err;
    };
    registerWebRpcRoute(
      { register: (r: WebServerRoute) => { captured = r; return () => undefined; } },
      ownerAdmit,
      '/wewrite-python',
      throwingHandler,
    );
    const req = fakeReq('POST', '/wewrite-python/snapshot', JSON.stringify({ type: 'client-request', rpcId: 'r-1', method: 'snapshot', payload: {} }));
    const res = fakeRes();
    await captured.handler(req, res);
    const written = res.end.mock.calls[0][0] as string;
    const parsed = JSON.parse(written);
    expect(parsed.result.ok).toBe(false);
    expect(parsed.result.error.message).not.toContain('SUPERSECRET');
    expect(parsed.result.error.message).not.toContain('secret.js');
    expect(parsed.result.error.message).toContain('token=****');
  });

  it('method 与 endpoint 不匹配 → 400', async () => {
    let captured!: WebServerRoute;
    registerWebRpcRoute(
      { register: (r: WebServerRoute) => { captured = r; return () => undefined; } },
      ownerAdmit,
      '/wewrite-python',
      okHandler,
    );
    const req = fakeReq('POST', '/wewrite-python/snapshot', JSON.stringify({ type: 'client-request', rpcId: 'r-1', method: 'other', payload: {} }));
    const res = fakeRes();
    await captured.handler(req, res);
    const written = res.end.mock.calls[0][0] as string;
    const parsed = JSON.parse(written);
    expect(parsed.result.ok).toBe(false);
  });

  it('非法通道 → 抛错', () => {
    expect(() =>
      registerWebRpcRoute(
        { register: () => () => undefined },
        ownerAdmit,
        'bad-channel',
        okHandler,
      ),
    ).toThrow();
  });
});
