/**
 * Web RPC 路由注册（rc.4 实测适配层）。
 *
 * rc.4 的 connection.rpc.handle() 内部会 owner.webServer.register()，其中 owner 是
 * connection 服务自身 fiber 的 ctx（其 inject 只有 credentials，没有 webServer），
 * 第三方插件调用会抛 "cannot get property webServer without inject"。因此本插件在
 * 自己的子 fiber（inject ['connection','webServer']）里直接向 webServer 注册 prefix
 * 路由，并复用 connection.admit() 完成 Host/Origin 信任与浏览器认证。
 *
 * wire 契约与 dsh-client-connection 一致：
 *   请求：{type:'client-request', rpcId, method, payload}（POST <channel>/<endpoint>）
 *   响应：{type:'server-response', rpcId, result:{ok:true,value}|{ok:false,error}}
 */

import type { IncomingMessage, ServerResponse } from 'node:http';
import type { ConnectionRpcResult, RpcHandler } from './types';

export interface WebServerRoute {
  readonly kind: 'prefix';
  readonly path: string;
  readonly handler: (req: IncomingMessage, res: ServerResponse) => Promise<void> | void;
}

export interface WebServerService {
  register(route: WebServerRoute): () => void;
}

export interface ConnectionAdmit {
  readonly peer: unknown;
}

export interface ConnectionAuthService {
  admit(req: IncomingMessage): { readonly peer: unknown } | { readonly rejection: 401 | 403 };
}

const CHANNEL_PATTERN = /^\/[A-Za-z0-9._~-]+$/;

interface ClientRequestEnvelope {
  readonly type?: unknown;
  readonly rpcId?: unknown;
  readonly method?: unknown;
  readonly payload?: unknown;
}

function readJsonBody(req: IncomingMessage): Promise<unknown> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = [];
    let size = 0;
    req.on('data', (chunk: Buffer) => {
      size += chunk.length;
      if (size > 64 * 1024 * 1024) {
        reject(new Error('请求体过大'));
        req.destroy();
        return;
      }
      chunks.push(chunk);
    });
    req.on('end', () => {
      const raw = Buffer.concat(chunks).toString('utf8');
      try {
        resolve(JSON.parse(raw));
      } catch {
        reject(new Error('请求体不是合法 JSON'));
      }
    });
    req.on('error', reject);
  });
}

function writeJson(res: ServerResponse, status: number, body: unknown): void {
  const payload = JSON.stringify(body);
  res.writeHead(status, { 'content-type': 'application/json' });
  res.end(payload);
}

/** 从 pathname 提取 endpoint：/wewrite-python/snapshot → snapshot。 */
function endpointFromPath(channel: string, pathname: string): string | undefined {
  if (!pathname.startsWith(`${channel}/`)) return undefined;
  const endpoint = pathname.slice(channel.length + 1);
  if (endpoint.split('/').some((segment) => segment === '' || segment === '.' || segment === '..' || !/^[A-Za-z0-9_$.-]+$/.test(segment))) {
    return undefined;
  }
  return endpoint;
}

/** 注册一个带认证的 prefix RPC 路由。返回 disposer。 */
export function registerWebRpcRoute(
  webServer: WebServerService,
  connection: ConnectionAuthService,
  channel: string,
  handler: RpcHandler,
): () => void {
  if (!CHANNEL_PATTERN.test(channel) || channel === '/api') {
    throw new Error(`connection: invalid or reserved RPC channel ${JSON.stringify(channel)}`);
  }
  return webServer.register({
    kind: 'prefix',
    path: channel,
    handler: async (req, res) => {
      const url = new URL(req.url ?? '/', 'http://dsh.internal');
      const method = req.method ?? 'GET';
      const endpoint = endpointFromPath(channel, url.pathname);

      if (endpoint === undefined) {
        writeJson(res, 404, { error: 'not found' });
        return;
      }
      if (method !== 'POST') {
        res.writeHead(405);
        res.end();
        return;
      }
      const admission = connection.admit(req);
      if ('rejection' in admission) {
        res.writeHead(admission.rejection);
        res.end(admission.rejection === 401 ? 'unauthorized' : 'forbidden');
        return;
      }
      if (req.headers['content-type']?.split(';', 1)[0]?.trim().toLowerCase() !== 'application/json') {
        writeJson(res, 415, { error: 'content type must be application/json' });
        return;
      }
      let body: unknown;
      try {
        body = await readJsonBody(req);
      } catch (error) {
        writeJson(res, 400, { error: error instanceof Error ? error.message : 'bad request' });
        return;
      }
      const envelope = body as ClientRequestEnvelope;
      const rpcId = typeof envelope.rpcId === 'string' ? envelope.rpcId : null;
      if (envelope.type !== 'client-request' || rpcId === null) {
        writeJson(res, 400, { type: 'server-response', rpcId: rpcId ?? 'invalid-request', result: { ok: false, error: { code: 'gateway/bad-request', message: 'invalid client-request message', details: {} } } });
        return;
      }
      if (envelope.method !== endpoint) {
        writeJson(res, 400, {
          type: 'server-response',
          rpcId,
          result: {
            ok: false,
            error: { code: 'gateway/bad-request', message: `method ${JSON.stringify(envelope.method)} does not match endpoint ${JSON.stringify(endpoint)}`, details: {} },
          },
        });
        return;
      }
      const abort = new AbortController();
      res.on('close', () => {
        if (!res.writableEnded) abort.abort();
      });
      let result: ConnectionRpcResult;
      try {
        result = await handler(endpoint, envelope.payload ?? {}, abort.signal, admission.peer as never);
      } catch (error) {
        result = { ok: false, error: { code: 'internal', message: error instanceof Error ? error.message : String(error), details: {} } };
      }
      writeJson(res, 200, { type: 'server-response', rpcId, result });
    },
  });
}
