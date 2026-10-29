/**
 * wewrite-bridge.ts（设计 §6/§7 范围第 7 条）：固定解释器 + JSON 子进程协议 + 子进程回收。
 *
 * - `spawn(executable, ['-m','dsh_wewrite_facade'], {shell:false})`，解释器由 profile 私有
 *   覆盖配置提供（WEWRITE_PYTHON / 配置 interpreter），不接受任意 argv/模块名。
 * - 单请求单响应，stdout 只允许 JSON，stderr 是脱敏诊断（不写进响应）。
 * - 超时档：普通 30s / 热榜 60s / 推送 120s；超时 TERM → 短时宽限 → KILL，只回收本次 pid。
 * - 响应字节上限 8 MiB，超限立即终止并返回明确诊断，不冒充子进程 JSON。
 */

import { spawn } from 'node:child_process';

export const PROTOCOL = 1;
export const MAX_OUTPUT_BYTES = 8 * 1024 * 1024;

export const TIMEOUT_NORMAL = 30_000;
export const TIMEOUT_HOTSPOTS = 60_000;
export const TIMEOUT_PUSH = 120_000;

const OP_TIMEOUTS: Readonly<Record<string, number>> = {
  'hotspots.fetch': TIMEOUT_HOTSPOTS,
  'draft.prepare': TIMEOUT_PUSH,
  'draft.send': TIMEOUT_PUSH,
  'draft.status': TIMEOUT_NORMAL,
};

export function timeoutFor(operation: string): number {
  return OP_TIMEOUTS[operation] ?? TIMEOUT_NORMAL;
}

export interface BridgeResult {
  /** 子进程 JSON 响应（stdout 合法 JSON 时为该对象，否则 null）。 */
  readonly response: { readonly protocol: number; readonly requestId: string; readonly ok: boolean; readonly data?: unknown; readonly error?: unknown } | null;
  readonly timedOut: boolean;
  /** 已写入 stdin 后超时（发送类操作可能已发生，标 unknown）。 */
  readonly timedOutAfterSend: boolean;
  readonly returncode: number | null;
  /** 本地诊断（IssueSchema 形状）；response 为 null 时调用方先看这里。 */
  readonly issue: { readonly code: string; readonly message: string; readonly blocking: boolean } | null;
}

export interface BridgeConfig {
  /** 固定 Python 解释器绝对路径。 */
  readonly interpreter: string;
  /** 唯一数据根（每个子进程在自身 env 设置相同固定 WEWRITE_HOME）。 */
  readonly home: string;
  /** 普通调用超时（毫秒）；默认按 operation 取档。 */
  readonly timeoutMs?: number;
}

export interface FacadeCall {
  readonly operation: string;
  readonly payload: unknown;
  readonly requestId: string;
}

function terminate(pid: number | undefined): void {
  if (pid === undefined) return;
  try {
    process.kill(pid, 'SIGTERM');
  } catch {
    // 已退出。
    return;
  }
  const killAt = Date.now() + 2000;
  const check = setInterval(() => {
    if (Date.now() >= killAt) {
      clearInterval(check);
      try {
        process.kill(pid, 'SIGKILL');
      } catch {
        // 已退出。
      }
      return;
    }
    try {
      process.kill(pid, 0);
    } catch {
      clearInterval(check);
    }
  }, 100);
  check.unref?.();
}

/** 固定解释器 spawn：shell:false，参数只有 `-m dsh_wewrite_facade`。 */
export async function runFacade(call: FacadeCall, config: BridgeConfig): Promise<BridgeResult> {
  const timeout = config.timeoutMs ?? timeoutFor(call.operation);
  const request = JSON.stringify({
    protocol: PROTOCOL,
    requestId: call.requestId,
    operation: call.operation,
    payload: call.payload ?? {},
  });

  const child = spawn(config.interpreter, ['-m', 'dsh_wewrite_facade'], {
    shell: false,
    stdio: ['pipe', 'pipe', 'pipe'],
    env: { ...process.env, WEWRITE_HOME: config.home },
  });

  let sent = false;
  try {
    child.stdin.write(request + '\n', 'utf8', () => undefined);
    sent = true;
  } catch {
    // 写入失败：子进程已退出，后续 stdout 读取出空。
  }
  try {
    child.stdin.end();
  } catch {
    // 忽略。
  }

  const result = await collect(child, timeout, sent);
  return result;
}

function collect(
  child: import('node:child_process').ChildProcess,
  timeout: number,
  sent: boolean,
): Promise<BridgeResult> {
  return new Promise((resolve) => {
    const stdoutChunks: Buffer[] = [];
    let stdoutBytes = 0;
    let stderrBytes = 0;
    let settled = false;
    let timedOut = false;
    let timedOutAfterSend = false;
    let returncode: number | null = null;

    const deadline = setTimeout(() => {
      timedOut = true;
      timedOutAfterSend = sent;
      terminate(child.pid ?? undefined);
    }, timeout);
    deadline.unref?.();

    child.stdout!.on('data', (chunk: Buffer) => {
      stdoutBytes += chunk.length;
      if (stdoutBytes > MAX_OUTPUT_BYTES) {
        if (!settled) {
          settled = true;
          clearTimeout(deadline);
          terminate(child.pid ?? undefined);
          resolve({
            response: null,
            timedOut: false,
            timedOutAfterSend: false,
            returncode: null,
            issue: { code: 'OUTPUT_TOO_LARGE', message: '子进程输出超限', blocking: true },
          });
        }
        return;
      }
      stdoutChunks.push(chunk);
    });

    child.stderr!.on('data', (chunk: Buffer) => {
      stderrBytes += chunk.length;
      // stderr 是脱敏诊断，不写进响应；只做字节上限截断。
    });

    child.on('error', (error) => {
      if (settled) return;
      settled = true;
      clearTimeout(deadline);
      resolve({
        response: null,
        timedOut: false,
        timedOutAfterSend: false,
        returncode: null,
        issue: { code: 'BRIDGE_SPAWN_FAILED', message: `子进程启动失败：${error.message}`, blocking: true },
      });
    });

    child.on('close', (code) => {
      returncode = code;
      if (settled) return;
      settled = true;
      clearTimeout(deadline);

      if (timedOut) {
        resolve({
          response: null,
          timedOut: true,
          timedOutAfterSend,
          returncode,
          issue: { code: 'TIMEOUT', message: '操作超时', blocking: true },
        });
        return;
      }

      if (code !== 0) {
        resolve({
          response: null,
          timedOut: false,
          timedOutAfterSend: false,
          returncode,
          issue: { code: 'BRIDGE_EXIT', message: `子进程退出码 ${code}`, blocking: true },
        });
        return;
      }

      const text = Buffer.concat(stdoutChunks).toString('utf8').trim();
      if (!text) {
        resolve({
          response: null,
          timedOut: false,
          timedOutAfterSend: false,
          returncode,
          issue: { code: 'BRIDGE_EMPTY', message: '子进程无输出', blocking: true },
        });
        return;
      }
      try {
        const parsed = JSON.parse(text) as BridgeResult['response'];
        resolve({ response: parsed, timedOut: false, timedOutAfterSend: false, returncode, issue: null });
      } catch {
        resolve({
          response: null,
          timedOut: false,
          timedOutAfterSend: false,
          returncode,
          issue: { code: 'BRIDGE_NOT_JSON', message: '子进程输出不是 JSON', blocking: true },
        });
      }
    });
  });
}
