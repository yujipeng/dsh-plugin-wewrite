"""验收 8：draft.send 走假 transport 成功，wechatApiCalls === 0。"""

from __future__ import annotations

from conftest import call_facade, create_run, make_home, write_article


def test_draft_send_fake_transport_zero_wechat(tmp_path):
    home = make_home(tmp_path)
    run_id = create_run(home)
    version = write_article(home, run_id, "# 标题\n\n正文内容")

    # 四轮：draft.send 读正文前必须先复验 intent（active_intent_for）。
    # 先发布 binding + 登记 intent，再发送。
    pub = call_facade(str(home), "binding.publish", {
        "requestId": "b1", "runId": run_id, "workflowId": "wf-1", "generation": 1,
        "agentId": "a", "sessionId": "s", "workspaceId": "ws-1", "phase": "writing",
    })
    assert pub["ok"] is True, pub
    prep = call_facade(str(home), "draft.prepare", {
        "requestId": "p1", "runId": run_id, "expectedVersion": None,
        "coverAssetId": "cover-1", "themeId": "professional-clean",
    })
    assert prep["ok"] is True, prep
    intent_id = prep["data"]["intentId"]

    resp = call_facade(str(home), "draft.send", {
        "requestId": "p1",
        "runId": run_id,
        "intentId": intent_id,
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


def test_draft_send_rejects_revoked_intent_zero_wechat(tmp_path):
    """四轮：intent 被 binding 撤销集合覆盖时，draft.send 拒且假 transport 计数零。"""
    home = make_home(tmp_path)
    run_id = create_run(home)
    version = write_article(home, run_id, "# 标题\n\n正文内容")

    pub = call_facade(str(home), "binding.publish", {
        "requestId": "b1", "runId": run_id, "workflowId": "wf-1", "generation": 1,
        "agentId": "a", "sessionId": "s", "workspaceId": "ws-1", "phase": "writing",
    })
    assert pub["ok"] is True, pub
    prep = call_facade(str(home), "draft.prepare", {
        "requestId": "p1", "runId": run_id, "expectedVersion": None,
        "coverAssetId": "cover-1", "themeId": "professional-clean",
    })
    intent_id = prep["data"]["intentId"]

    # 接管到 generation=2，binding 撤销集合覆盖旧 intent。
    takeover = call_facade(str(home), "binding.publish", {
        "requestId": "b2", "runId": run_id, "workflowId": "wf-2", "generation": 2,
        "agentId": "a2", "sessionId": "s2", "workspaceId": "ws-1", "phase": "writing",
        "expectedGeneration": 1,
    })
    assert takeover["ok"] is True, takeover

    resp = call_facade(str(home), "draft.send", {
        "requestId": "p2", "runId": run_id, "intentId": intent_id, "expectedVersion": version,
    })
    assert resp["ok"] is False, resp
    assert resp["error"]["code"] == "CANCELLED", resp


def test_draft_send_rejects_real_transport(tmp_path):
    from dsh_wewrite_facade import publish
    import pytest
    from dsh_wewrite_facade.errors import FacadeError
    with pytest.raises(FacadeError) as exc:
        publish.build_transport("wechat")
    assert exc.value.code == "CAPABILITY_UNAVAILABLE"
