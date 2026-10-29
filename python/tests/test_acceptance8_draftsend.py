"""验收 8：draft.send 走假 transport 成功，wechatApiCalls === 0。

S4 更新：正文/封面/审稿按 S4 预检口径构造（200+ 字正文 + 真实封面资产 + 通过审稿）。
"""

from __future__ import annotations

from conftest import (
    call_facade,
    create_run,
    make_home,
    make_publishable_run,
)


def _prepare(home, run_id, cover_asset_id, theme="professional-clean"):
    return call_facade(str(home), "draft.prepare", {
        "requestId": "p1", "runId": run_id, "expectedVersion": None,
        "coverAssetId": cover_asset_id, "themeId": theme,
    })


def test_draft_send_fake_transport_zero_wechat(tmp_path):
    home = make_home(tmp_path)
    run_id = create_run(home)
    version, cover = make_publishable_run(home, run_id)

    prep = _prepare(home, run_id, cover["assetId"])
    assert prep["ok"] is True, prep
    intent_id = prep["data"]["intentId"]

    resp = call_facade(str(home), "draft.send", {
        "requestId": "p1",
        "runId": run_id,
        "intentId": intent_id,
        "expectedVersion": version,
        "transport": "fake",
        "context": {"runId": run_id, "workflowId": "wf-a", "generation": 1},
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
    """intent 被 binding 撤销集合覆盖时，draft.send 拒且假 transport 计数零。"""
    home = make_home(tmp_path)
    run_id = create_run(home)
    version, cover = make_publishable_run(home, run_id)

    prep = _prepare(home, run_id, cover["assetId"])
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
        "context": {"runId": run_id, "workflowId": "wf-a", "generation": 1},
    })
    assert resp["ok"] is False, resp
    assert resp["error"]["code"] == "CANCELLED", resp


def test_draft_send_rejects_missing_context(tmp_path):
    """裁定 B：context 是内部必填字段，缺失即 INVALID_INPUT（浏览器/模型不能自报执行身份）。"""
    home = make_home(tmp_path)
    run_id = create_run(home)
    version, cover = make_publishable_run(home, run_id)

    prep = _prepare(home, run_id, cover["assetId"])
    intent_id = prep["data"]["intentId"]

    resp = call_facade(str(home), "draft.send", {
        "requestId": "p2", "runId": run_id, "intentId": intent_id, "expectedVersion": version,
    })
    assert resp["ok"] is False, resp
    assert resp["error"]["code"] == "INVALID_INPUT", resp


def test_draft_send_rejects_context_runid_mismatch(tmp_path):
    """裁定 B：context.runId 必须等于 runId，否则拒绝。"""
    home = make_home(tmp_path)
    run_id = create_run(home)
    version, cover = make_publishable_run(home, run_id)

    prep = _prepare(home, run_id, cover["assetId"])
    intent_id = prep["data"]["intentId"]

    resp = call_facade(str(home), "draft.send", {
        "requestId": "p2", "runId": run_id, "intentId": intent_id, "expectedVersion": version,
        "context": {"runId": "20261028-120000-abcdef", "workflowId": "wf-a", "generation": 1},
    })
    assert resp["ok"] is False, resp
    assert resp["error"]["code"] == "INVALID_INPUT", resp


def test_build_transport_whitelist(tmp_path):
    """S4：真实 transport 可构造但需凭据；白名单守卫拒绝群发端点。"""
    from dsh_wewrite_facade import publish
    from dsh_wewrite_facade.errors import FacadeError
    import pytest

    # 真实 transport 需要凭据。
    with pytest.raises(FacadeError) as exc:
        publish.build_transport("wechat")
    assert exc.value.code == "FORBIDDEN"

    with pytest.raises(FacadeError) as exc:
        publish.build_transport("wechat", {"appid": "a", "secret": "s"})
        publish.assert_whitelisted("cgi-bin/message/mass/send")
    assert exc.value.code == "FORBIDDEN"
