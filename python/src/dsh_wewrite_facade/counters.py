"""两个独立计数器（设计 §6 / 父 issue 验收口径 5）：wechatApiCalls 与 remoteFetchCalls 分开统计。

- wechatApiCalls：令牌/上传/draft/add 等微信 API 调用（本 stage 恒为 0，真实实现属 S4）。
- remoteFetchCalls：远程图片抓取（安全素材获取）。
两个不得合并成一个总数。
"""

from __future__ import annotations

import threading


class CallCounters:
    """进程级线程安全计数器。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._wechat = 0
        self._remote = 0

    def inc_wechat(self) -> None:
        with self._lock:
            self._wechat += 1

    def inc_remote(self) -> None:
        with self._lock:
            self._remote += 1

    def wechat(self) -> int:
        with self._lock:
            return self._wechat

    def remote(self) -> int:
        with self._lock:
            return self._remote

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return {"wechatApiCalls": self._wechat, "remoteFetchCalls": self._remote}


# 进程级单例（每次子进程一条请求，但保留单例便于测试注入与断言）。
_default = CallCounters()


def get_counters() -> CallCounters:
    return _default


def reset_counters() -> None:
    global _default
    _default = CallCounters()
