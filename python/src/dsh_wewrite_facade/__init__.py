"""dsh_wewrite_facade —— 插件与 WeWrite 之间的唯一受控 Python 通道。

独立包名，禁止遮蔽上游 ``wewrite`` 包。JSON 子进程协议 + 只读投影 +
写锁与路径安全 + 假 transport 的 draft.send。
"""

from __future__ import annotations

__version__ = "0.1.0"
