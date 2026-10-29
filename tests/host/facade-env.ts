/**
 * facade 证据环境探针（JEE-1956 返修 2/3）：证据用例不得静默 skip。
 *
 * 之前的 `facadeAvailable()` 只探测 `FACADE_PYTHON` 能否 `import dsh_wewrite_facade`，
 * 不探测 `PYTHONPATH` 与 `wewrite` 运行时依赖——导致只设 `FACADE_PYTHON` 时证据用例
 * 全部 skip、vitest 仍报 passed，形成「测试全绿却无证据」的通道。
 *
 * 现在 `requireFacade()` 做一次真实 `run.create` 端到端探针：缺解释器、缺 PYTHONPATH、
 * 缺 wewrite 依赖任一项都会在这里**显式失败**（抛错），而不是静默 skip。
 */

import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { runFacade } from '@/host/wewrite-bridge';

export const REPO = resolve(__dirname, '..', '..');
export const FACADE_PYTHON = process.env.FACADE_PYTHON ?? resolve(REPO, '.venv', 'bin', 'python');

/** 真实端到端探针；成功返回 interpreter 路径，失败抛错（证据环境不可用即显式失败）。 */
export async function requireFacade(): Promise<string> {
  const home = mkdtempSync(join(tmpdir(), 'wewrite-facade-probe-'));
  const result = await runFacade(
    {
      operation: 'run.create',
      requestId: 'facade-probe',
      payload: {
        requestId: 'facade-probe',
        topic: '证据环境探针',
        mode: 'draft',
        visualMode: 'none',
        themeId: 'professional-clean',
      },
    },
    { interpreter: FACADE_PYTHON, home },
  );
  if (result.response?.ok === true) return FACADE_PYTHON;

  const detail = result.issue?.message
    ?? (result.response?.error as { message?: string } | undefined)?.message
    ?? '未知错误';
  throw new Error(
    `facade 证据环境不可用（不得静默 skip）：FACADE_PYTHON=${FACADE_PYTHON}，` +
    `PYTHONPATH=${process.env.PYTHONPATH ?? '(未设置)'}。` +
    `请按 README 安装依赖（pip install -e python，含 wewrite==4.2.1 全量依赖），` +
    `并在运行 TS 证据用例时同时设置 FACADE_PYTHON=<venv>/bin/python 与 PYTHONPATH=python/src。` +
    `探针 run.create 失败原因：${detail}`,
  );
}
