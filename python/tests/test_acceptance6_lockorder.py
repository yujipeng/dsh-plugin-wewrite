"""验收 6：锁顺序——构造反序获取场景，断言 run→home 反序被拒（或抛明确错误码），
固定交错，并对错误真正断言（不允许收集后不校验）。"""

from __future__ import annotations

import threading

import pytest

from conftest import create_run, make_home
from dsh_wewrite_facade import paths, storage
from dsh_wewrite_facade.errors import FacadeError


def _set_home_env(home):
    # 直接调用 storage 锁需在进程内设置 WEWRITE_HOME（paths.home() 读它）。
    import os
    os.environ["WEWRITE_HOME"] = str(home)


def test_reverse_lock_order_rejected(tmp_path):
    """先持 run 锁再取 home 锁（run→home 反序）必须被拒绝。"""
    home = make_home(tmp_path)
    run_id = create_run(home)
    _set_home_env(home)

    error: list[BaseException] = []
    got_home = threading.Event()

    def reverse():
        try:
            with storage.run_lock(run_id, create=False):
                # 反序：在 run 锁内取 home 锁。
                with storage.home_lock(create=False):
                    got_home.set()
        except BaseException as exc:  # noqa: BLE001
            error.append(exc)

    t = threading.Thread(target=reverse)
    t.start()
    t.join(timeout=10)
    assert not t.is_alive(), "反序获取死锁"
    assert not got_home.is_set(), "反序获取不应成功取得 home 锁"
    assert len(error) == 1, f"应恰好抛出一个明确错误，实际 {error}"
    exc = error[0]
    assert isinstance(exc, FacadeError)
    assert exc.code == "FORBIDDEN"
    assert "锁顺序" in str(exc)


def test_fixed_order_ok(tmp_path):
    """home → run 顺序正常，不报错。"""
    home = make_home(tmp_path)
    run_id = create_run(home)
    _set_home_env(home)

    done = threading.Event()
    errors: list[BaseException] = []

    def fixed():
        try:
            with storage.home_lock(create=False):
                with storage.run_lock(run_id, create=False):
                    done.set()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    t = threading.Thread(target=fixed)
    t.start()
    t.join(timeout=10)
    assert not t.is_alive()
    assert done.is_set(), "固定顺序应成功"
    assert errors == [], f"固定顺序不应报错：{errors}"
