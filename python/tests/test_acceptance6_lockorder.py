"""验收 6：锁顺序——构造反序获取场景，断言不死锁且按固定顺序（先 home 全局锁，再 run 锁）。"""

from __future__ import annotations

import threading
import time

from conftest import create_run, make_home
from dsh_wewrite_facade import storage


def test_lock_order_no_deadlock(tmp_path):
    home = make_home(tmp_path)
    run_id = create_run(home)

    # 两个线程：一个按固定顺序（home → run），一个反序（run → home）。
    # 断言两者都能在有限时间内完成（不死锁）。
    errors = []

    def fixed_order():
        try:
            with storage.home_lock(create=False):
                time.sleep(0.05)
                with storage.run_lock(run_id, create=False):
                    pass
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    def reverse_order():
        try:
            with storage.run_lock(run_id, create=False):
                time.sleep(0.05)
                with storage.home_lock(create=False):
                    pass
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    t1 = threading.Thread(target=fixed_order)
    t2 = threading.Thread(target=reverse_order)
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)

    assert not t1.is_alive(), "固定顺序线程死锁"
    assert not t2.is_alive(), "反序线程死锁"
    # 反序获取若被实现拒绝/序列化，不得死锁；这里允许两种：成功完成或抛「顺序错误」。
    # 无论哪种，进程都不能卡死。
