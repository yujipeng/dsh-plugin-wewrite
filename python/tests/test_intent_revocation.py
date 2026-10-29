"""返修 6：旧 intent 撤销事务。

- binding.publish 接管时把旧 generation 的未消费 intent 标记 revoked（同一 run 锁内）。
- binding.revoke-intents（cancel 路径）撤销全部未消费 intent。
- 撤销后 active_intent_for 返回 None（旧 intent 的外部调用计数为零）。
- 正文/报告/状态字节不变（撤销只改 .dsh-workbench/intents.json）。
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from conftest import call_facade, create_run, make_home, write_article

from dsh_wewrite_facade import intents


def _read_intents(home, run_id):
    """直接读 intents 注册表（测试进程内 WEWRITE_HOME 与 facade 子进程一致）。"""
    import json
    p = home / "runs" / run_id / ".dsh-workbench" / "intents.json"
    if not p.exists():
        return []
    data = json.loads(p.read_text(encoding="utf-8"))
    return data.get("intents", [])


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


def _record_intent(home, run_id, intent_id, workflow, generation, version):
    # 直接写 intents 注册表（模拟 draft.prepare 已登记一个未消费 intent）。
    import json
    run_dir = home / "runs" / run_id
    (run_dir / ".dsh-workbench").mkdir(parents=True, exist_ok=True)
    (run_dir / ".dsh-workbench" / "intents.json").write_text(
        json.dumps({"intents": [{
            "intentId": intent_id, "runId": run_id, "workflowId": workflow,
            "generation": generation, "articleVersion": version, "expiresAt": "", "revoked": False,
        }]}, ensure_ascii=False),
        encoding="utf-8",
    )


def test_takeover_revokes_old_intent(tmp_path):
    home = make_home(tmp_path)
    run_id = create_run(home)
    version = write_article(home, run_id, "种子正文")
    assert _publish(home, run_id, 1, workflow="wf-old")["ok"] is True

    # 旧 generation=1 有一个未消费 intent。
    _record_intent(home, run_id, "intent-old", "wf-old", 1, version)
    assert len(_read_intents(home, run_id)) == 1

    article_before = (home / "runs" / run_id / "article.md").read_bytes()
    state_before = (home / "runs" / run_id / "state.yaml").read_bytes()

    # 接管：generation=1 → 2，binding 撤销集合应包含旧 intent（唯一权威）。
    assert _publish(home, run_id, 2, workflow="wf-new", expected=1)["ok"] is True

    # 四轮：binding.revokedIntentIds 是唯一权威；intents.json 的 revoked 标记不再被重放（读面无写盘）。
    binding_data = _read_binding(home, run_id)
    assert "intent-old" in binding_data.get("revokedIntentIds", [])
    # 旧 intent 复验返回 None（active_intent_for 读 binding 撤销集合）。
    assert intents.active_intent_for(run_id, "intent-old", workflow_id="wf-new", generation=2) is None
    assert intents.active_intent_for(run_id, "intent-old", workflow_id="wf-old", generation=1) is None
    # 正文/状态字节不变。
    assert (home / "runs" / run_id / "article.md").read_bytes() == article_before
    assert (home / "runs" / run_id / "state.yaml").read_bytes() == state_before


def test_cancel_revokes_all_intents(tmp_path):
    home = make_home(tmp_path)
    run_id = create_run(home)
    version = write_article(home, run_id, "种子正文")
    assert _publish(home, run_id, 1, workflow="wf-a")["ok"] is True
    _record_intent(home, run_id, "intent-1", "wf-a", 1, version)

    resp = call_facade(str(home), "binding.revoke-intents", {"runId": run_id})
    assert resp["ok"] is True, resp
    assert resp["data"]["revoked"] == 1

    # 四轮：binding.revokedIntentIds 是唯一权威。
    binding_data = _read_binding(home, run_id)
    assert "intent-1" in binding_data.get("revokedIntentIds", [])
    assert intents.active_intent_for(run_id, "intent-1", workflow_id="wf-a", generation=1) is None


def _read_binding(home, run_id):
    import json
    p = home / "runs" / run_id / ".dsh-workbench" / "binding.json"
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))
