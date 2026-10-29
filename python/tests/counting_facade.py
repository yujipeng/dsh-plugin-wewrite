"""测试辅助：安装文件计数 adapter 工厂后走标准 facade 协议入口。

供多进程 journal 唯一键用例使用：每个子进程在本模块 main() 里
`publish.set_adapter_factory(...)` 注入一个跨进程落文件加锁计数的替身，
随后调用标准 `protocol.run_stdio()` 处理单请求单响应。

替身的 `create_draft` 每次调用向 `$CREATE_DRAFT_COUNT_FILE` 追加一行（flock 加锁），
测试读该文件行数即跨进程共享的 draft/add 调用总次数。替身不触碰 `wechatApiCalls`
（fake 路径硬断言真实微信调用为零仍成立）。
"""

from __future__ import annotations

import fcntl
import os
import sys


def main() -> int:
    for p in os.environ.get("PYTHONPATH", "").split(os.pathsep):
        if p and p not in sys.path:
            sys.path.insert(0, p)

    from dsh_wewrite_facade import publish
    from dsh_wewrite_facade.protocol import run_stdio

    count_file = os.environ["CREATE_DRAFT_COUNT_FILE"]

    class FileCountingAdapter:
        def __init__(self) -> None:
            self.calls = 0

        def create_draft(self, *, title: str, html: str, digest: str, thumb_media_id, author) -> dict:
            self.calls += 1
            with open(count_file, "a") as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                try:
                    f.write("1\n")
                    f.flush()
                finally:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            return {"mediaId": f"fake-file-{os.getpid()}", "dryRun": True, "title": title}

    publish.set_adapter_factory(lambda kind, credentials: FileCountingAdapter())
    return run_stdio()


if __name__ == "__main__":
    sys.exit(main())
