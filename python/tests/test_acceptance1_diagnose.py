"""验收 1：干净虚拟环境安装固定版本上游 + facade，diagnose 响应。"""

from __future__ import annotations

import json

from conftest import call_facade, make_home


def test_diagnose_responds(tmp_path):
    home = make_home(tmp_path)
    resp = call_facade(str(home), "diagnose", {})
    assert resp["ok"] is True
    data = resp["data"]
    assert data["protocol"] == 1
    assert data["wewriteVersion"] == "4.2.1"
    assert data["homeInitialized"] is False
    # 两个独立计数器，不得合并成一个总数。
    assert "counters" in data
    assert set(data["counters"]) == {"wechatApiCalls", "remoteFetchCalls"}
    assert data["counters"]["wechatApiCalls"] == 0
    assert data["counters"]["remoteFetchCalls"] == 0
