"""验收 7：子进程超时与回收——TERM 无效 → KILL 生效；run_facade() 真实超时链路。"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time

from dsh_wewrite_facade import runner


def test_terminate_kill_when_term_ignored(tmp_path):
    """TERM 被忽略时，KILL 必须生效并回收子进程。"""
    # 子进程忽略 SIGTERM，只有 SIGKILL 能终止。
    proc = subprocess.Popen(
        [sys.executable, "-c",
         "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"]
    )
    pid = proc.pid
    rc = runner._terminate(proc, pid)
    assert proc.poll() is not None, "子进程未回收"
    assert rc is not None, "应返回 returncode"
    try:
        os.kill(pid, 0)
        alive = True
    except ProcessLookupError:
        alive = False
    assert not alive, "子进程未回收"


def test_terminate_wait_when_already_exited(tmp_path):
    """poll() 已退出的分支也要 wait() 回收。"""
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    pid = proc.pid
    rc = runner._terminate(proc, pid)
    assert rc == 0
    assert proc.returncode == 0


def test_run_facade_real_timeout(tmp_path, monkeypatch):
    """run_facade() 真实超时链路：挂起子进程，返回 timed_out。"""
    # 造一个挂起的 dsh_wewrite_facade 模块，放在 PYTHONPATH 最前。
    fake_pkg = tmp_path / "fakepkg" / "dsh_wewrite_facade"
    fake_pkg.mkdir(parents=True)
    (fake_pkg / "__init__.py").write_text("", encoding="utf-8")
    (fake_pkg / "__main__.py").write_text(
        "import time\ntime.sleep(30)\n", encoding="utf-8"
    )
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "fakepkg"))

    result = runner.run_facade(
        home=str(tmp_path),
        operation="run.detail",
        payload={"runId": "20261028-120000-abcdef"},
        request_id="r",
        timeout=1,
    )
    assert result.timed_out is True
    assert result.response is None


def test_timeout_tiers(tmp_path):
    assert runner.timeout_for("hotspots.fetch") == 60
    assert runner.timeout_for("draft.send") == 120
    assert runner.timeout_for("draft.prepare") == 120
    assert runner.timeout_for("run.detail") == 30
    assert runner.timeout_for("draft.status") == 30
