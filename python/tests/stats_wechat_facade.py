"""测试辅助：伪造微信数据接口响应后走标准 facade 协议入口。

供 ``stats.fetch`` 有凭证分支的正向（成功返回逐篇 articles）与负向（每日 errcode）
用例使用。环境变量：

- ``FAKE_TOKEN_STATUS``：令牌接口 HTTP 状态码（默认 200）。
- ``FAKE_TOKEN_OK``：置 ``0`` 则令牌请求返回 ``{"errcode":40013}``（HTTP 仍按
  ``FAKE_TOKEN_STATUS``）；否则返回 ``{"access_token":"fake-token"}``。
- ``FAKE_DAILY``：JSON 数组字符串，每个元素为 ``{"status": <code>, "body": <obj>}``，
  按调用顺序逐日作为 ``requests.post``（datacube/getarticlesummary）的响应。

本脚本不改任何生产代码路径，只在子进程内 monkeypatch ``requests.get/post``，
随后调用标准 ``protocol.run_stdio()`` 处理单请求单响应。
"""

from __future__ import annotations

import json
import os
import sys

import requests as _requests_mod


def main() -> int:
    for p in os.environ.get("PYTHONPATH", "").split(os.pathsep):
        if p and p not in sys.path:
            sys.path.insert(0, p)

    import requests

    from dsh_wewrite_facade.protocol import run_stdio

    daily: list[dict] = json.loads(os.environ["FAKE_DAILY"])

    def _resp(status, body):
        r = type("FakeResponse", (), {})()
        r.status_code = status
        r.json = lambda: body

        def raise_for_status():
            if not (200 <= status < 300):
                raise _requests_mod.HTTPError(f"{status} Server Error")

        r.raise_for_status = raise_for_status
        return r

    def fake_get(url, **kwargs):
        status = int(os.environ.get("FAKE_TOKEN_STATUS", "200"))
        if os.environ.get("FAKE_TOKEN_OK") == "0":
            return _resp(status, {"errcode": 40013, "errmsg": "invalid appsecret"})
        return _resp(status, {"access_token": "fake-token"})

    def fake_post(url, **kwargs):
        if not daily:
            raise AssertionError("FAKE_DAILY 消耗完毕，测试夹具 days 与响应数不匹配")
        item = daily.pop(0)
        status = int(item["status"])
        body = item["body"]
        return _resp(status, body)

    requests.get = fake_get
    requests.post = fake_post
    return run_stdio()


if __name__ == "__main__":
    sys.exit(main())
