"""测试辅助：把 home 锁的「读 → 改 → 写」窗口拉长后走标准 facade 协议入口。

供并发回填丢更新用例使用：子进程在 ``backfill_history`` 每次读取 history.yaml 后、
写入前，如果设置了 ``BACKFILL_HOLD_MS``，则 sleep 该毫秒数，让两个子进程的读-改-写
窗口稳定重叠，验证「同一把 home 锁」是否真的覆盖了整段区间。

这里通过 monkeypatch ``stats.read_history`` 注入延迟（sleep 只发生在测试辅助脚本内，
绝不写进生产代码路径）。随后调用标准 ``protocol.run_stdio()``。
"""

from __future__ import annotations

import os
import sys
import time


def main() -> int:
    for p in os.environ.get("PYTHONPATH", "").split(os.pathsep):
        if p and p not in sys.path:
            sys.path.insert(0, p)

    from dsh_wewrite_facade import stats
    from dsh_wewrite_facade.protocol import run_stdio

    hold_ms = int(os.environ.get("BACKFILL_HOLD_MS", "0"))
    original_read = stats.read_history

    def delayed_read():
        result = original_read()
        if hold_ms > 0:
            time.sleep(hold_ms / 1000.0)
        return result

    stats.read_history = delayed_read
    return run_stdio()


if __name__ == "__main__":
    sys.exit(main())
