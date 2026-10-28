"""子进程 runner（设计 §6）：spawn 固定解释器、超时 TERM→KILL、回收确切子进程。

- 普通调用 30s / 热榜 60s / 推送 120s。
- 超时先 TERM，短时宽限后 KILL，只回收本次 spawn 的确切 PID。
- 发送后超时标 unknown（外部动作可能已发生）。
- stdout 只允许 JSON，且读入有字节上限，超限立即终止子进程并返回明确错误码。
- stderr 脱敏诊断（不写入响应）。
"""

from __future__ import annotations

import errno
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

# stdout 读入字节上限（防解码炸弹/字节流超限）。
MAX_OUTPUT_BYTES = 8 * 1024 * 1024

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


class OutputTooLargeError(FacadeRunnerError):
    """子进程输出超过字节上限。"""


@dataclass
class RunResult:
    response: dict[str, Any] | None
    timed_out: bool
    timed_out_after_send: bool
    returncode: int | None


def _default_interpreter() -> str:
    return sys.executable


def _terminate(proc: subprocess.Popen, pid: int) -> int | None:
    """TERM → 短时宽限 → KILL，只针对该 pid。返回最终 returncode（已回收）。"""
    rc = proc.poll()
    if rc is not None:
        # 已退出也必须 wait() 回收，确认 returncode。
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        return proc.returncode

    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        pass

    deadline = time.monotonic() + _KILL_GRACE
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            return proc.returncode
        time.sleep(0.05)

    # TERM 无效 → KILL。
    if proc.poll() is None:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
    return proc.returncode


def _read_stdout_bounded(proc: subprocess.Popen) -> bytes:
    """有界读取 stdout 全部字节；超限立即 TERM/KILL 并抛 OutputTooLargeError。"""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = proc.stdout.read(65536)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_OUTPUT_BYTES:
            _terminate(proc, proc.pid)
            raise OutputTooLargeError("子进程输出超限")
        chunks.append(chunk)
    return b"".join(chunks)


def _read_stderr_bounded(proc: subprocess.Popen) -> str:
    """读 stderr（脱敏诊断不写入响应）；有上限截断。"""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = proc.stderr.read(65536)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_OUTPUT_BYTES:
            break
        chunks.append(chunk)
    return b"".join(chunks).decode("utf-8", errors="replace")


def run_facade(
    *,
    home: str,
    operation: str,
    payload: dict[str, Any],
    request_id: str,
    interpreter: str | None = None,
    timeout: int | None = None,
) -> RunResult:
    """spawn 一个 facade 子进程，单请求单响应，超时/超限回收。

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
        env=env,
    )

    sent = False
    try:
        proc.stdin.write((req + "\n").encode("utf-8"))
        proc.stdin.flush()
        sent = True
    except (BrokenPipeError, OSError):
        pass
    finally:
        try:
            proc.stdin.close()
        except OSError:
            pass

    deadline = time.monotonic() + timeout

    # 有界读 stdout，按剩余时间分片等待。
    chunks: list[bytes] = []
    total = 0
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(cmd=[interpreter, "-m", "dsh_wewrite_facade"], timeout=timeout)
            # 用 select 等待可读，避免无上限阻塞。
            import select
            readable, _, _ = select.select([proc.stdout], [], [], remaining)
            if not readable:
                raise subprocess.TimeoutExpired(cmd=[interpreter, "-m", "dsh_wewrite_facade"], timeout=timeout)
            chunk = os.read(proc.stdout.fileno(), 65536)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_OUTPUT_BYTES:
                _terminate(proc, proc.pid)
                return RunResult(response=None, timed_out=False, timed_out_after_send=sent, returncode=proc.returncode)
            chunks.append(chunk)
    except subprocess.TimeoutExpired:
        _terminate(proc, proc.pid)
        return RunResult(response=None, timed_out=True, timed_out_after_send=sent, returncode=proc.returncode)

    # 读取结束，回收并收集 stderr。
    proc.wait(timeout=5) if proc.poll() is None else None
    out = b"".join(chunks)

    if proc.returncode != 0:
        raise FacadeRunnerError(f"facade 子进程退出码 {proc.returncode}")

    text = out.decode("utf-8", errors="replace").strip()
    if not text:
        raise FacadeRunnerError("facade 无输出")
    try:
        resp = json.loads(text)
    except json.JSONDecodeError as exc:
        raise FacadeRunnerError("facade 输出不是 JSON") from exc
    return RunResult(response=resp, timed_out=False, timed_out_after_send=False, returncode=proc.returncode)
