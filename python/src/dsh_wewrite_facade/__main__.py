"""``python -m dsh_wewrite_facade`` 入口：单请求单响应，stdout 只允许 JSON。"""

from __future__ import annotations

import os
import sys


def main() -> int:
    # 子进程自身环境设固定 WEWRITE_HOME（不写 host 全局环境）。
    # WEWRITE_HOME 已由父进程 spawn 时注入 env；这里仅确保默认值一致。
    if not os.environ.get("WEWRITE_HOME"):
        os.environ["WEWRITE_HOME"] = os.path.join(os.path.expanduser("~"), ".wewrite")

    from .protocol import run_stdio
    return run_stdio()


if __name__ == "__main__":
    sys.exit(main())
