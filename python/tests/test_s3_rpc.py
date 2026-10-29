"""S3 业务 RPC（facade 侧新增）：run.list 分页与 binding.get 只读。

- run.list 只读返回 RunSummary 分页（简化：全量 + nextCursor=None）。
- binding.get 只读返回绑定或 null，不创建目录/文件。
"""

from __future__ import annotations

import pytest

from conftest import call_facade, create_run, make_home


def test_run_list_returns_created_run(tmp_path):
    home = make_home(tmp_path)
    run_id = create_run(home, topic="列表演示")
    resp = call_facade(str(home), "run.list", {"limit": 10})
    assert resp["ok"] is True, resp
    data = resp["data"]
    assert data["nextCursor"] is None
    assert any(item["runId"] == run_id for item in data["items"])


def test_binding_get_unbound_returns_null(tmp_path):
    home = make_home(tmp_path)
    run_id = create_run(home)
    resp = call_facade(str(home), "binding.get", {"runId": run_id})
    assert resp["ok"] is True, resp
    assert resp["data"]["binding"] is None
    # 只读不创建 .dsh-workbench。
    assert not (home / "runs" / run_id / ".dsh-workbench").exists()
