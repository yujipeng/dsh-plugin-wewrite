"""测试辅助：伪造微信数据接口响应后走标准 facade 协议入口。

供 ``stats.fetch`` 有凭证分支的正向（成功返回逐篇 articles）与负向（每日 errcode）
用例使用。环境变量：

- ``FAKE_TOKEN_OK``：置 ``0`` 则令牌请求失败（返回 ``{"errcode":40013}``）；否则
  返回 ``{"access_token":"fake-token"}``。
- ``FAKE_DAILY``：JSON 数组字符串，按调用顺序逐日作为 ``requests.post``
  （datacube/getarticlesummary）的响应体。

本脚本不改任何生产代码路径，只在子进程内 monkeypatch ``requests.get/post``，
随后调用标准 ``protocol.run_stdio()`` 处理单请求单响应。
"""

from __future__ import annotations

import json
import os
import sys


def main() -> int:
    for p in os.environ.get("PYTHONPATH", "").split(os.pathsep):
        if p and p not in sys.path:
            sys.path.insert(0, p)

    import requests

    from dsh_wewrite_facade.protocol import run_stdio

    daily: list[dict] = json.loads(os.environ["FAKE_DAILY"])

    def _resp(body):
        r = type("FakeResponse", (), {})()
        r.json = lambda: body
        return r

    def fake_get(url, **kwargs):
        if os.environ.get("FAKE_TOKEN_OK") == "0":
            return _resp({"errcode": 40013, "errmsg": "invalid appsecret"})
        return _resp({"access_token": "fake-token"})

    def fake_post(url, **kwargs):
        if not daily:
            raise AssertionError("FAKE_DAILY 消耗完毕，测试夹具 days 与响应数不匹配")
        return _resp(daily.pop(0))

    requests.get = fake_get
    requests.post = fake_post
    return run_stdio()


if __name__ == "__main__":
    sys.exit(main())
