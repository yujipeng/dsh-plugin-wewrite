"""子进程 runner（设计 §6）：spawn 固定解释器、超时 TERM→KILL、回收确切子进程。

- 普通调用 30s / 热榜 60s / 推送 120s。
- 超时先 TERM，短时宽限后 KILL，只回收本次 spawn 的确切 PID。
- 发送后超时标 unknown（外部动作可能已发生）。
- stdout 只允许 JSON；stderr 脱敏诊断（不写入响应）。
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Any

# 超时档（秒）。
TIMEOUT_NORMAL = 30
TIMEOUT_HOTSPOTS = 60
TIMEOUT_PUSH = 120

# TERM 后 KILL 的宽限（秒）。
_KILL_GRACE = 2.0

# 超时档按 operation 选择。
_OP_TIMEOUTS = {
    "hotspots.fetch": TIMEOUT_HOTSPOTS,
    "draft.send": TIMEOUT_PUSH,
    "draft.prepare": TIMEOUT_PUSH,
    "draft.status": TIMEOUT_NORMAL,
}


def timeout_for(operation: str) -> int:
    return _OP_TIMEOUTS.get(operation, TIMEOUT_NORMAL)


class FacadeRunnerError(Exception):
    """runner 层失败（脱敏中文）。"""


@dataclass
class RunResult:
    response: dict[str, Any] | None
    timed_out: bool
    timed_out_after_send: bool
    returncode: int | None


def _default_interpreter() -> str:
    return sys.executable


def _terminate(proc: subprocess.Popen, pid: int) -> None:
    """TERM → 短时宽限 → KILL，只针对该 pid。"""
    if proc.poll() is not None:
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        pass
    deadline = time.monotonic() + _KILL_GRACE
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return
        time.sleep(0.05)
    if proc.poll() is None:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
        proc.wait(timeout=5)


def run_facade(
    *,
    home: str,
    operation: str,
    payload: dict[str, Any],
    request_id: str,
    interpreter: str | None = None,
    timeout: int | None = None,
) -> RunResult:
    """spawn 一个 facade 子进程，单请求单响应，超时回收。

    - timeout=None 时按 operation 取档位。
    - 发送后超时（已写入 stdin 才超时）标 timed_out_after_send。
    """
    interpreter = interpreter or _default_interpreter()
    timeout = timeout if timeout is not None else timeout_for(operation)

    req = json.dumps({
        "protocol": 1,
        "requestId": request_id,
        "operation": operation,
        "payload": payload,
    }, ensure_ascii=False)

    env = os.environ.copy()
    env["WEWRITE_HOME"] = home

    proc = subprocess.Popen(
        [interpreter, "-m", "dsh_wewrite_facade"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )

    sent = False
    try:
        proc.stdin.write(req + "\n")
        proc.stdin.flush()
        sent = True
    except BrokenPipeError:
        # 子进程提前退出。
        pass

    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _terminate(proc, proc.pid)
        return RunResult(response=None, timed_out=True, timed_out_after_send=sent, returncode=proc.returncode)

    if proc.returncode != 0:
        raise FacadeRunnerError(f"facade 子进程退出码 {proc.returncode}")

    out = out.strip()
    if not out:
        raise FacadeRunnerError("facade 无输出")
    try:
        resp = json.loads(out)
    except json.JSONDecodeError as exc:
        raise FacadeRunnerError("facade 输出不是 JSON") from exc
    return RunResult(response=resp, timed_out=False, timed_out_after_send=False, returncode=proc.returncode)
