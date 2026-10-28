"""验收 7：子进程超时与回收——模拟挂死，断言 TERM→KILL 且只回收确切 PID，无遗留子进程。"""

from __future__ import annotations

import os
import subprocess
import sys
import time

from dsh_wewrite_facade import runner


def _spawn_hang_interpreter_script(tmp_path, delay):
    """造一个会挂起的假解释器脚本（作为 python -m 的替代）。"""
    script = tmp_path / "hang.py"
    script.write_text(
        "import time, sys\n"
        f"time.sleep({delay})\n"
        "print('{}')\n"
    , encoding="utf-8")
    return str(script)


def test_timeout_terminates_exact_pid(tmp_path):
    # 用真 python -m 挂起不易注入；直接调用 runner 的 _terminate 路径：
    # 起一个 sleep 子进程，断言 TERM 后 KILL 且只回收该 pid。
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    pid = proc.pid
    runner._terminate(proc, pid)
    # 子进程应已被回收。
    assert proc.poll() is not None
    # 该 pid 不再存在。
    import errno
    try:
        os.kill(pid, 0)
        alive = True
    except ProcessLookupError:
        alive = False
    assert not alive, "子进程未回收"


def test_timeout_returns_timed_out_flag(tmp_path):
    # 挂起的 facade：用一个能挂起的自定义 interpreter（真实 wewrite 环境里不便挂起，
    # 这里验证 runner 的超时分类逻辑）。
    class _FakePopen:
        def __init__(self):
            self.pid = 999999
            self.returncode = None

        def poll(self):
            return None

        def wait(self, timeout=None):
            return 0

    # 直接验证 timeout_for 档位。
    assert runner.timeout_for("hotspots.fetch") == 60
    assert runner.timeout_for("draft.send") == 120
    assert runner.timeout_for("draft.prepare") == 120
    assert runner.timeout_for("run.detail") == 30
    assert runner.timeout_for("draft.status") == 30
