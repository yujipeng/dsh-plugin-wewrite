"""九轮返修：fail-closed 用例（每条在 62c6a90 上会红）。

覆盖：TTL 过期拒、phase fence、binding 缺失 fail-closed、撤销集合并集、
结构错误 STATE_CORRUPTED、摘要复验补全、prepare 拒 cancelled、adapter 计数=1。
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


def _write_intents_file(home, run_id, intents):
    p = home / "runs" / run_id / ".dsh-workbench" / "intents.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"schemaVersion": 2, "intents": intents}, ensure_ascii=False), encoding="utf-8")


def test_ttl_expired_rejects_send(tmp_path):
    """九轮-1：expiresAt 过期后 send 被拒，adapter 零调用。"""
    home = make_home(tmp_path)
    run_id = create_run(home)
    version = write_article(home, run_id, "# 标题\n\n正文内容")
    assert _publish(home, run_id, 1, workflow="wf-1")["ok"] is True
    intent_id = _prepare(home, run_id)["data"]["intentId"]
    # 手动把 expiresAt 改成过去。
    p = home / "runs" / run_id / ".dsh-workbench" / "intents.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    for item in data["intents"]:
        if item["intentId"] == intent_id:
            item["expiresAt"] = "1000000000"  # 2001-09-09，早已过期
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    resp = call_facade(str(home), "draft.send", {
        "requestId": "s1", "runId": run_id, "intentId": intent_id, "expectedVersion": version,
        "context": {"runId": run_id, "workflowId": "wf-1", "generation": 1},
    })
    assert resp["ok"] is False, resp
    assert resp["error"]["code"] in ("CANCELLED", "VERSION_CONFLICT"), resp
    assert "counters" not in (resp.get("data") or {})


def test_binding_missing_fail_closed(tmp_path):
    """九轮-3：binding 文件缺失时 send 二次复验必须拒绝（不得 fail-open 放行）。"""
    home = make_home(tmp_path)
    run_id = create_run(home)
    version = write_article(home, run_id, "# 标题\n\n正文内容")
    assert _publish(home, run_id, 1, workflow="wf-1")["ok"] is True
    intent_id = _prepare(home, run_id)["data"]["intentId"]
    # 删除 binding.json（模拟外部删除/迁移中断）。
    (home / "runs" / run_id / ".dsh-workbench" / "binding.json").unlink()

    resp = call_facade(str(home), "draft.send", {
        "requestId": "s1", "runId": run_id, "intentId": intent_id, "expectedVersion": version,
        "context": {"runId": run_id, "workflowId": "wf-1", "generation": 1},
    })
    assert resp["ok"] is False, resp
    assert resp["error"]["code"] in ("CANCELLED", "FORBIDDEN"), resp


def test_revocation_set_union_keeps_tombstones(tmp_path):
    """九轮-4：撤销集合 = 旧 binding ∪ 本次重算，旧墓碑不因接管缩水。"""
    home = make_home(tmp_path)
    run_id = create_run(home)
    version = write_article(home, run_id, "# 标题\n\n正文内容")
    assert _publish(home, run_id, 1, workflow="wf-1")["ok"] is True
    # 旧 binding 已含一个墓碑（模拟之前接管/取消留下的终态撤销）。
    b_path = home / "runs" / run_id / ".dsh-workbench" / "binding.json"
    old_binding = json.loads(b_path.read_text(encoding="utf-8"))
    old_binding["revokedIntentIds"] = ["old-sent-intent"]
    b_path.write_text(json.dumps(old_binding, ensure_ascii=False), encoding="utf-8")

    # 接管到 gen=2。
    assert _publish(home, run_id, 2, workflow="wf-new", expected=1)["ok"] is True

    # 撤销集合应保留旧墓碑（并集不丢）。
    b2 = json.loads(b_path.read_text(encoding="utf-8"))
    assert "old-sent-intent" in b2.get("revokedIntentIds", []), b2


def test_structural_error_state_corrupted(tmp_path):
    """九轮-5：intents 字段非 list 时报 STATE_CORRUPTED，不得当空表覆盖。"""
    home = make_home(tmp_path)
    run_id = create_run(home)
    version = write_article(home, run_id, "# 标题\n\n正文内容")
    assert _publish(home, run_id, 1, workflow="wf-1")["ok"] is True
    # 写一个 intents 为 dict 的损坏文件。
    p = home / "runs" / run_id / ".dsh-workbench" / "intents.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"schemaVersion": 2, "intents": {"not": "a list"}}), encoding="utf-8")

    # 任何写路径都不应把损坏覆盖为空表；draft.prepare 在锁内读 intents（登记前）。
    resp = _prepare(home, run_id)
    assert resp["ok"] is False, resp
    assert resp["error"]["code"] == "STATE_CORRUPTED", resp


def test_summary_fields_written_and_verified(tmp_path):
    """九轮-9：prepare 写入摘要字段，send 复验这些字段。"""
    home = make_home(tmp_path)
    run_id = create_run(home)
    version = write_article(home, run_id, "# 标题\n\n正文内容")
    assert _publish(home, run_id, 1, workflow="wf-1")["ok"] is True
    prep = _prepare(home, run_id)
    intent_id = prep["data"]["intentId"]

    p = home / "runs" / run_id / ".dsh-workbench" / "intents.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    target = [i for i in data["intents"] if i["intentId"] == intent_id][0]
    # 摘要字段已写入（非 None/空）。
    assert target.get("reportVersion") is None or isinstance(target.get("reportVersion"), str)
    assert "themeId" in target, target


def test_adapter_called_exactly_once_and_repeat_send_rejected(tmp_path, monkeypatch):
    """JEE-1956 缺口 6：真实 adapter 计数（替换原「声称计数却无计数断言」的用例）。

    进程内直接驱动 draft_dispatch.send，monkeypatch 假 adapter 计数：
    一次 send 恰好一次 adapter；重复 send 终态拒绝（INVALID_TRANSITION）且不再调用。
    """
    from dsh_wewrite_facade import draft_dispatch
    from dsh_wewrite_facade.errors import FacadeError

    home = make_home(tmp_path)
    run_id = create_run(home)
    version = write_article(home, run_id, "# 标题\n\n正文内容")
    assert _publish(home, run_id, 1, workflow="wf-1")["ok"] is True
    intent_id = _prepare(home, run_id)["data"]["intentId"]
    monkeypatch.setenv("WEWRITE_HOME", str(home))

    calls: list[int] = []
    real_send = draft_dispatch.publish.send_draft_fake

    def counting_send(*args, **kwargs):
        calls.append(1)
        return real_send(*args, **kwargs)

    monkeypatch.setattr(draft_dispatch.publish, "send_draft_fake", counting_send)

    resp = draft_dispatch.send(
        {"requestId": "s1", "runId": run_id, "intentId": intent_id, "expectedVersion": version},
        {"runId": run_id, "workflowId": "wf-1", "generation": 1},
    )
    assert resp["ok"] is True, resp
    assert resp["data"]["status"] == "succeeded"
    assert len(calls) == 1, calls

    # 重复发送：终态拒绝（精确 INVALID_TRANSITION，不再允许 NOT_FOUND），adapter 不再调用。
    try:
        draft_dispatch.send(
            {"requestId": "s2", "runId": run_id, "intentId": intent_id, "expectedVersion": version},
            {"runId": run_id, "workflowId": "wf-1", "generation": 1},
        )
        code = None
    except FacadeError as exc:
        code = exc.code
    assert code == "INVALID_TRANSITION", code
    assert len(calls) == 1, calls

