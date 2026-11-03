"""测试辅助：注入 os.replace 失败后走标准 facade 协议入口。

供 style.put 原子写负例使用：在临时文件已成功生成、进入 replace 步骤时才抛
OSError（由环境变量 REPLACE_FAIL_FILE 指定要失败的目标文件名，这里是 style.yaml）。
异常在 storage._write_file_atomically 的 except 分支里 unlink 临时文件后继续上抛，
facade 协议层把 OSError 收敛为业务失败 INTERNAL，不泄露栈。
"""

from __future__ import annotations

import os
import sys


def main() -> int:
    for p in os.environ.get("PYTHONPATH", "").split(os.pathsep):
        if p and p not in sys.path:
            sys.path.insert(0, p)

    from dsh_wewrite_facade import storage
    from dsh_wewrite_facade.protocol import run_stdio

    fail_file = os.environ["REPLACE_FAIL_FILE"]
    original_replace = storage.os.replace

    def failing_replace(src, dst, *args, **kwargs):
        # 只在目标文件命中指定文件时失败；其余（如备份 style.yaml.bak）正常放行。
        if dst == fail_file:
            raise OSError("injected replace failure")
        return original_replace(src, dst, *args, **kwargs)

    storage.os.replace = failing_replace
    return run_stdio()


if __name__ == "__main__":
    sys.exit(main())
