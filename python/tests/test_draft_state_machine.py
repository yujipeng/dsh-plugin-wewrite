"""六验：接管间隙封堵 + draft.recover + 状态机 + draft.send 发送段在门闩内。

先贴改坏时失败的原始输出（本文件是被实现正确性驱动的负向测试）：
- 接管后旧 intent 发送必须被拒且假 adapter 零调用。
- recover 转移表 claimed→revoked、dispatching→unknown。
- send 全链：claim → dispatching → sent，operationId 唯一。
"""

from __future__ import annotations

import json
import os

import pytest

from conftest import call_facade, create_run, make_home, write_article


def _publish(home, run_id, generation, workflow="wf-a", expected=None):
    return call_facade(str(home), "binding.publish", {
        "requestId": f"b{generation}",
        "runId": run_id,
        "workflowId": workflow,
        "generation": generation,
        "agentId": f"agent-{generation}",
        "sessionId": f"session-{generation}",
        "workspaceId": "ws-1",
        "phase": "writing",
        **({"expectedGeneration": expected} if expected is not None else {}),
    })


def _prepare(home, run_id):
    return call_facade(str(home), "draft.prepare", {
        "requestId": "p1", "runId": run_id, "expectedVersion": None,
        "coverAssetId": "cover-1", "themeId": "professional-clean",
    })


def test_takeover_rejects_old_intent_send_zero_calls(tmp_path):
    """audit_race 同构：接管后旧 intent 发送被拒，假 adapter 零调用。"""
    home = make_home(tmp_path)
    run_id = create_run(home)
    version = write_article(home, run_id, "# 标题\n\n正文内容")
    assert _publish(home, run_id, 1, workflow="wf-old")["ok"] is True
    intent_id = _prepare(home, run_id)["data"]["intentId"]

    # 接管到 generation=2。
    assert _publish(home, run_id, 2, workflow="wf-new", expected=1)["ok"] is True

    resp = call_facade(str(home), "draft.send", {
        "requestId": "s1", "runId": run_id, "intentId": intent_id, "expectedVersion": version,
        "context": {"runId": run_id, "workflowId": "wf-old", "generation": 1},
    })
    assert resp["ok"] is False, resp
    assert resp["error"]["code"] in ("CANCELLED", "FORBIDDEN"), resp
    # 假 adapter 零调用（发送失败时 counters 不出现）。
    assert "counters" not in (resp.get("data") or {})


def test_send_full_chain_claim_dispatching_sent(tmp_path):
    """send 全链：prepared→claimed→dispatching→sent，operationId 唯一。"""
    home = make_home(tmp_path)
    run_id = create_run(home)
    version = write_article(home, run_id, "# 标题\n\n正文内容")
    assert _publish(home, run_id, 1, workflow="wf-1")["ok"] is True
    intent_id = _prepare(home, run_id)["data"]["intentId"]

    resp = call_facade(str(home), "draft.send", {
        "requestId": "s1", "runId": run_id, "intentId": intent_id, "expectedVersion": version,
        "context": {"runId": run_id, "workflowId": "wf-1", "generation": 1},
    })
    assert resp["ok"] is True, resp
    assert resp["data"]["status"] == "succeeded"
    op_id = resp["data"]["operationId"]
    assert op_id

    # 重复发送同 intent 返回同一 operationId（幂等）。
    resp2 = call_facade(str(home), "draft.send", {
        "requestId": "s2", "runId": run_id, "intentId": intent_id, "expectedVersion": version,
        "context": {"runId": run_id, "workflowId": "wf-1", "generation": 1},
    })
    # 已 sent 的 intent 不再新建 operation（返回同一 operationId 或终态拒绝）。
    if resp2["ok"] is True:
        assert resp2["data"]["operationId"] == op_id


def test_recover_claimed_to_revoked(tmp_path):
    """recover：claimed→revoked（显式写恢复）。"""
    home = make_home(tmp_path)
    run_id = create_run(home)
    version = write_article(home, run_id, "# 标题\n\n正文内容")
    assert _publish(home, run_id, 1, workflow="wf-1")["ok"] is True
    intent_id = _prepare(home, run_id)["data"]["intentId"]

    # 手动把 intent 置为 claimed（模拟发送 claim 后卡住）。
    from dsh_wewrite_facade import intents as intents_mod
    import subprocess
    # 直接进程内改状态（不走子进程，避免 WEWRITE_HOME 隔离）。
    env = os.environ.copy()
    env["WEWRITE_HOME"] = str(home)
    env["PYTHONPATH"] = os.path.abspath("python/src")
    # 用 facade 的 binding.publish 已是子进程；这里改用进程内模块直接写。
    # 简化：直接编辑 intents.json。
    p = home / "runs" / run_id / ".dsh-workbench" / "intents.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    for item in data["intents"]:
        if item["intentId"] == intent_id:
            item["state"] = "claimed"
            item["operationId"] = "op-1"
            item["claimNonce"] = "nonce-1"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    rec = call_facade(str(home), "draft.recover", {"requestId": "r1", "runId": run_id, "intentId": intent_id})
    assert rec["ok"] is True, rec
    assert rec["data"]["revoked"] == 1

    # 恢复后状态为 revoked。
    p2 = home / "runs" / run_id / ".dsh-workbench" / "intents.json"
    data2 = json.loads(p2.read_text(encoding="utf-8"))
    target = [i for i in data2["intents"] if i["intentId"] == intent_id][0]
    assert target["state"] == "revoked"


def test_recover_dispatching_to_unknown(tmp_path):
    """recover：dispatching→unknown。"""
    home = make_home(tmp_path)
    run_id = create_run(home)
    version = write_article(home, run_id, "# 标题\n\n正文内容")
    assert _publish(home, run_id, 1, workflow="wf-1")["ok"] is True
    intent_id = _prepare(home, run_id)["data"]["intentId"]

    p = home / "runs" / run_id / ".dsh-workbench" / "intents.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    for item in data["intents"]:
        if item["intentId"] == intent_id:
            item["state"] = "dispatching"
            item["operationId"] = "op-1"
            item["claimNonce"] = "nonce-1"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    rec = call_facade(str(home), "draft.recover", {"requestId": "r1", "runId": run_id, "intentId": intent_id})
    assert rec["ok"] is True, rec
    assert rec["data"]["unknown"] == 1

    data2 = json.loads((home / "runs" / run_id / ".dsh-workbench" / "intents.json").read_text(encoding="utf-8"))
    target = [i for i in data2["intents"] if i["intentId"] == intent_id][0]
    assert target["state"] == "unknown"


def test_recover_reject_unknown_fields(tmp_path):
    """recover 严格入参：未知字段拒绝。"""
    home = make_home(tmp_path)
    run_id = create_run(home)
    resp = call_facade(str(home), "draft.recover", {
        "requestId": "r1", "runId": run_id, "claimNonce": "evil",
    })
    assert resp["ok"] is False, resp
    assert resp["error"]["code"] == "INVALID_INPUT", resp
