"""验收 8：draft.send 走假 transport 成功，wechatApiCalls === 0。"""

from __future__ import annotations

from conftest import call_facade, create_run, make_home, write_article


def test_draft_send_fake_transport_zero_wechat(tmp_path):
    home = make_home(tmp_path)
    run_id = create_run(home)
    version = write_article(home, run_id, "# 标题\n\n正文内容")

    resp = call_facade(str(home), "draft.send", {
        "requestId": "p1",
        "runId": run_id,
        "intentId": "intent-1",
        "expectedVersion": version,
    })
    assert resp["ok"] is True, resp
    data = resp["data"]
    assert data["status"] == "succeeded"
    assert data["dryRun"] is True
    assert data["mediaId"].startswith("fake-")
    # 硬约束：真实微信调用计数为零，两个计数器分离。
    assert data["counters"]["wechatApiCalls"] == 0
    assert data["counters"]["remoteFetchCalls"] == 0


def test_draft_send_rejects_real_transport(tmp_path):
    from dsh_wewrite_facade import publish
    import pytest
    from dsh_wewrite_facade.errors import FacadeError
    with pytest.raises(FacadeError) as exc:
        publish.build_transport("wechat")
    assert exc.value.code == "CAPABILITY_UNAVAILABLE"
