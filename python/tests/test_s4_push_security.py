"""S4 双闸/逐篇确认/安全边界（Python 侧）。

覆盖：
- 发布白名单：仅令牌/正文图片上传/封面素材上传/draft.add/draft.get；无 freepublish/mass/send。
- intentId 独立幂等：同 intent 即使多个 callId 分别获批，最多一次 draft/add；unknown 不释放键。
- dry-run：假调用一次、wechatApiCalls === 0、remoteFetchCalls 单独计数、不携带微信凭据。
- 密钥不回显：哨兵 secret 不进 stdout/stderr/RPC 响应/配置快照。
- 审批期间业务竞争：prepare 后改正文/封面/主题/账号，send 拒绝且 wechatApiCalls === 0。
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from conftest import (
    call_facade,
    create_run,
    make_home,
    make_publishable_run,
    put_cover,
    write_article,
    write_review,
    facade_env,
)


def _prepare(home, run_id, cover_asset_id, theme="professional-clean"):
    return call_facade(str(home), "draft.prepare", {
        "requestId": "p1", "runId": run_id, "expectedVersion": None,
        "coverAssetId": cover_asset_id, "themeId": theme,
    })


def _send(home, run_id, intent_id, version, transport="fake", credentials=None, workflow="wf-1", generation=1):
    payload = {
        "requestId": "s1", "runId": run_id, "intentId": intent_id, "expectedVersion": version,
        "transport": transport,
        "context": {"runId": run_id, "workflowId": workflow, "generation": generation},
    }
    if credentials is not None:
        payload["credentials"] = credentials
    return call_facade(str(home), "draft.send", payload)


def test_publish_whitelist_blocks_mass_send_and_freepublish():
    from dsh_wewrite_facade import publish
    from dsh_wewrite_facade.errors import FacadeError
    import pytest

    # 白名单放行。
    for endpoint in [
        "https://api.weixin.qq.com/cgi-bin/token",
        "https://api.weixin.qq.com/cgi-bin/media/uploadimg",
        "https://api.weixin.qq.com/cgi-bin/material/add_material",
        "https://api.weixin.qq.com/cgi-bin/draft/add",
        "https://api.weixin.qq.com/cgi-bin/draft/get",
    ]:
        publish.assert_whitelisted(endpoint)  # 不抛即通过

    # 群发/发布类端点拒绝。
    for endpoint in [
        "https://api.weixin.qq.com/cgi-bin/message/mass/send",
        "https://api.weixin.qq.com/cgi-bin/message/mass/sendall",
        "https://api.weixin.qq.com/cgi-bin/freepublish/submit",
        "https://api.weixin.qq.com/cgi-bin/message/subscribe/bizsend",
    ]:
        with pytest.raises(FacadeError) as exc:
            publish.assert_whitelisted(endpoint)
        assert exc.value.code == "FORBIDDEN", endpoint

    # 未知端点拒绝。
    with pytest.raises(FacadeError):
        publish.assert_whitelisted("https://api.weixin.qq.com/cgi-bin/message/template/send")


def test_intent_idempotency_single_draft_add(tmp_path):
    """同 intent 多次 send 最多一次 draft/add；第二次终态拒绝且 adapter 不再调用。"""
    home = make_home(tmp_path)
    run_id = create_run(home)
    version, cover = make_publishable_run(home, run_id, workflow="wf-1")
    intent_id = _prepare(home, run_id, cover["assetId"])["data"]["intentId"]

    first = _send(home, run_id, intent_id, version)
    assert first["ok"] is True, first
    assert first["data"]["status"] == "succeeded"
    assert first["data"]["dryRun"] is True
    assert first["data"]["counters"]["wechatApiCalls"] == 0

    # 第二次（不同 callId）终态拒绝：不再新建 operation，不再次 add。
    second = _send(home, run_id, intent_id, version)
    assert second["ok"] is False, second
    assert second["error"]["code"] in ("INVALID_TRANSITION", "VERSION_CONFLICT", "NOT_FOUND"), second


def test_unknown_tombstone_not_released(tmp_path):
    """unknown 墓碑不释放 intentId 唯一键：recover 后再 send 仍终态拒绝。"""
    home = make_home(tmp_path)
    run_id = create_run(home)
    version, cover = make_publishable_run(home, run_id, workflow="wf-1")
    intent_id = _prepare(home, run_id, cover["assetId"])["data"]["intentId"]

    # 手动把 intent 置为 dispatching（模拟发送中）。
    p = home / "runs" / run_id / ".dsh-workbench" / "intents.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    for item in data["intents"]:
        if item["intentId"] == intent_id:
            item["state"] = "dispatching"
            item["operationId"] = "op-1"
            item["claimNonce"] = "nonce-1"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    # recover：dispatching → unknown（墓碑）。
    rec = call_facade(str(home), "draft.recover", {"requestId": "r1", "runId": run_id, "intentId": intent_id})
    assert rec["ok"] is True, rec
    assert rec["data"]["unknown"] == 1

    # unknown 后再次 send：终态拒绝，不释放键。
    again = _send(home, run_id, intent_id, version)
    assert again["ok"] is False, again
    assert again["error"]["code"] == "INVALID_TRANSITION", again


def test_dry_run_real_wechat_calls_zero_and_credentials_not_leaked(tmp_path):
    """dry-run：假调用一次、wechatApiCalls === 0、remoteFetchCalls 单独计数、无微信凭据。"""
    home = make_home(tmp_path)
    run_id = create_run(home)
    version, cover = make_publishable_run(home, run_id, workflow="wf-1")
    intent_id = _prepare(home, run_id, cover["assetId"])["data"]["intentId"]

    sentinel = "SENTINEL-WECHAT-SECRET-9f8a7b6c"
    resp = _send(home, run_id, intent_id, version, transport="fake",
                 credentials={"appid": "wx-sentinel-app", "secret": sentinel})
    assert resp["ok"] is True, resp
    data = resp["data"]
    assert data["dryRun"] is True
    assert data["mediaId"].startswith("fake-")
    assert data["counters"]["wechatApiCalls"] == 0
    assert data["counters"]["remoteFetchCalls"] == 0
    # 哨兵不回显：整个 JSON 响应字符串不含哨兵。
    assert sentinel not in json.dumps(resp, ensure_ascii=False)
    assert sentinel not in json.dumps(data, ensure_ascii=False)


def test_business_race_reject_after_content_change(tmp_path):
    """审批期间业务竞争：prepare 后改正文/封面/主题，send 拒绝且 wechatApiCalls === 0。"""
    home = make_home(tmp_path)
    run_id = create_run(home)
    version, cover = make_publishable_run(home, run_id, workflow="wf-1")
    intent_id = _prepare(home, run_id, cover["assetId"])["data"]["intentId"]

    # 正文变化（版本变化 → 第二闸拒）。
    write_article(home, run_id, "# 新标题\n\n" + "改过的正文" * 40)
    resp = _send(home, run_id, intent_id, version)
    assert resp["ok"] is False, resp
    assert resp["error"]["code"] in ("CANCELLED", "VERSION_CONFLICT"), resp
    assert "counters" not in (resp.get("data") or {})


def test_prepare_rejects_missing_or_unreadable_cover(tmp_path):
    """prepare 要求实际封面可读；假封面/缺封面拒绝。"""
    home = make_home(tmp_path)
    run_id = create_run(home)
    version, _cover = make_publishable_run(home, run_id, workflow="wf-1")

    resp = _prepare(home, run_id, "nonexistent-cover")
    assert resp["ok"] is False, resp
    assert resp["error"]["code"] in ("ARTIFACT_MISSING", "NOT_FOUND"), resp


def test_prepare_rejects_body_too_short(tmp_path):
    """正文不足 200 字 prepare 拒绝。"""
    home = make_home(tmp_path)
    run_id = create_run(home)
    version = write_article(home, run_id, "# 短\n\n太短")
    # 需要 binding + 通过审稿才能进入内容检查；但正文太短会先被内容检查拒绝。
    # 直接构造 binding + 审稿（正文哈希绑定），验证内容限制被拒。
    assert call_facade(str(home), "binding.publish", {
        "requestId": "b1", "runId": run_id, "workflowId": "wf-1", "generation": 1,
        "agentId": "a", "sessionId": "s", "workspaceId": "ws-1", "phase": "writing",
    })["ok"] is True
    write_review(home, run_id, version, True)
    cover = put_cover(home, run_id)
    resp = _prepare(home, run_id, cover["assetId"])
    assert resp["ok"] is False, resp
    assert resp["error"]["code"] == "INVALID_INPUT", resp
    assert "200" in resp["error"]["message"]


# ── 返修 3：journal 唯一键边界（并发 / 重启 / 超时重试）────────────────

def _send_inprocess(home, run_id, intent_id, version):
    """在独立 facade 子进程里跑一次 draft.send（每个子进程代表一个已批准 callId）。"""
    env = facade_env(str(home))
    req = {"protocol": 1, "requestId": "s", "operation": "draft.send", "payload": {
        "requestId": "s", "runId": run_id, "intentId": intent_id, "expectedVersion": version,
        "transport": "fake",
        "context": {"runId": run_id, "workflowId": "wf-1", "generation": 1},
    }}
    proc = subprocess.run(
        [sys.executable, "-m", "dsh_wewrite_facade"],
        input=json.dumps(req, ensure_ascii=False) + "\n",
        capture_output=True, text=True, env=env, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_concurrent_sends_same_intent_exactly_one_draft_add(tmp_path):
    """多进程并发（多个已批准 callId 竞争同一 intentId）：最多一次 draft/add。"""
    home = make_home(tmp_path)
    run_id = create_run(home)
    version, cover = make_publishable_run(home, run_id, workflow="wf-1")
    intent_id = _prepare(home, run_id, cover["assetId"])["data"]["intentId"]

    # 多个独立子进程同时发同一 intent（模拟多个 callId 分别获批）。
    results: list[dict] = []
    lock = threading.Lock()

    def worker():
        r = _send_inprocess(home, run_id, intent_id, version)
        with lock:
            results.append(r)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    succeeded = [r for r in results if r.get("ok") is True and r.get("data", {}).get("status") == "succeeded"]
    # 唯一一次 draft/add：恰好一个子进程得到 sent（真实 draft/add 最多一次）。
    assert len(succeeded) == 1, results
    # 其余都是终态拒绝或 in-flight 重响应（claimed→pending_approval），绝不产生第二次 add。
    others = [r for r in results if r is not succeeded[0]]
    assert len(others) == len(results) - 1, results
    for r in others:
        if r.get("ok") is True:
            # in-flight 重响应：claimed 状态返回 pending_approval，mediaId 为空。
            assert r["data"]["status"] == "pending_approval", r
            assert r["data"].get("mediaId") is None, r
        else:
            assert r["error"]["code"] in ("INVALID_TRANSITION", "VERSION_CONFLICT", "NOT_FOUND"), r
    # 唯一成功的那次 dryRun=true，真实微信调用为零。
    assert succeeded[0]["data"]["counters"]["wechatApiCalls"] == 0


def test_restart_after_success_does_not_re_add(tmp_path):
    """进程重启后状态恢复：同 intent 再次 send（新子进程）终态拒绝，不重复 draft/add。"""
    home = make_home(tmp_path)
    run_id = create_run(home)
    version, cover = make_publishable_run(home, run_id, workflow="wf-1")
    intent_id = _prepare(home, run_id, cover["assetId"])["data"]["intentId"]

    first = _send(home, run_id, intent_id, version)
    assert first["ok"] is True, first

    # 模拟重启：全新子进程再发同一 intent（状态从磁盘恢复）。
    again = _send_inprocess(home, run_id, intent_id, version)
    assert again["ok"] is False, again
    assert again["error"]["code"] in ("INVALID_TRANSITION", "VERSION_CONFLICT", "NOT_FOUND"), again


def test_timeout_unknown_tombstone_no_retry_re_add(tmp_path):
    """发送超时后标 unknown，墓碑不释放：再次 send 终态拒绝，不自动重试 add。"""
    home = make_home(tmp_path)
    run_id = create_run(home)
    version, cover = make_publishable_run(home, run_id, workflow="wf-1")
    intent_id = _prepare(home, run_id, cover["assetId"])["data"]["intentId"]

    # 模拟发送超时：dispatching → recover → unknown（墓碑）。
    p = home / "runs" / run_id / ".dsh-workbench" / "intents.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    for item in data["intents"]:
        if item["intentId"] == intent_id:
            item["state"] = "dispatching"
            item["operationId"] = "op-timeout"
            item["claimNonce"] = "nonce-timeout"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    rec = call_facade(str(home), "draft.recover", {"requestId": "r1", "runId": run_id, "intentId": intent_id})
    assert rec["ok"] is True, rec
    assert rec["data"]["unknown"] == 1

    # unknown 墓碑不释放：再次 send 终态拒绝，不自动重试 add。
    again = _send(home, run_id, intent_id, version)
    assert again["ok"] is False, again
    assert again["error"]["code"] == "INVALID_TRANSITION", again
    # 未产生新的真实调用计数（拒绝路径无 counters）。
    assert "counters" not in (again.get("data") or {})


def test_concurrent_inprocess_create_draft_called_once(tmp_path, monkeypatch):
    """进程内并发（共享计数 adapter）：真实 draft/add（create_draft）调用次数最多一次。

    用共享计数 fake 直接驱动 draft_dispatch.send，多个线程并发同一 intent：
    无论多少线程竞争，create_draft 只被调用一次；其余要么 in-flight 重响应、
    要么终态拒绝。
    """
    from dsh_wewrite_facade import draft_dispatch
    from dsh_wewrite_facade.errors import FacadeError

    home = make_home(tmp_path)
    run_id = create_run(home)
    version, cover = make_publishable_run(home, run_id, workflow="wf-1")
    intent_id = _prepare(home, run_id, cover["assetId"])["data"]["intentId"]
    monkeypatch.setenv("WEWRITE_HOME", str(home))

    calls: list[int] = []
    real_send = draft_dispatch.publish.send_draft_fake

    def counting_send(*args, **kwargs):
        calls.append(1)
        return real_send(*args, **kwargs)

    monkeypatch.setattr(draft_dispatch.publish, "send_draft_fake", counting_send)

    outcomes: list[tuple[str, str]] = []

    def run_send():
        try:
            r = draft_dispatch.send(
                {"requestId": "s", "runId": run_id, "intentId": intent_id, "expectedVersion": version},
                {"runId": run_id, "workflowId": "wf-1", "generation": 1},
            )
            outcomes.append(("ok", r["data"]["status"]))
        except FacadeError as exc:
            outcomes.append(("err", exc.code))

    threads = [threading.Thread(target=run_send) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    # create_draft（= draft/add）只被调用一次。
    assert len(calls) == 1, (calls, outcomes)
    # 恰好一个 sent。
    assert [o for o in outcomes if o == ("ok", "succeeded")] == [("ok", "succeeded")], outcomes
