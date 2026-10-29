"""返修二轮-3：binding + 撤销集合单一原子提交的可恢复性（进程内测试）。"""

from __future__ import annotations

import json
import os

import pytest

from dsh_wewrite_facade import binding as binding_mod, intents as intents_mod, storage


@pytest.fixture()
def seeded_home(tmp_path, monkeypatch):
    """进程内构造 home（不走子进程），返回 (home, run_id, version)。"""
    home = tmp_path / "WEWRITE_HOME"
    home.mkdir(parents=True)
    monkeypatch.setenv("WEWRITE_HOME", str(home))

    # 用 facade 子进程 create_run 建立目录/state（避免进程内复杂组装），
    # 后续 publish/read 全走进程内函数（便于 monkeypatch）。
    import subprocess
    import sys
    env = os.environ.copy()
    env["PYTHONPATH"] = os.path.abspath("python/src")
    req = {"protocol": 1, "requestId": "c", "operation": "run.create",
           "payload": {"requestId": "c", "topic": "t", "mode": "draft", "visualMode": "none", "themeId": "professional-clean"}}
    proc = subprocess.run([sys.executable, "-m", "dsh_wewrite_facade"], input=json.dumps(req) + "\n",
                          capture_output=True, text=True, env=env, timeout=60)
    assert proc.returncode == 0, proc.stderr
    run_id = json.loads(proc.stdout)["data"]["runId"]

    # 写正文（version）
    import hashlib
    article = "种子正文".encode("utf-8")
    (home / "runs" / run_id / "article.md").write_bytes(article)
    version = hashlib.sha256(article).hexdigest()

    return home, run_id, version


def _publish(run_id, generation, workflow="wf-a", expected=None):
    return binding_mod.publish_binding(
        run_id,
        workflow_id=workflow,
        generation=generation,
        agent_id=f"agent-{generation}",
        session_id=f"session-{generation}",
        workspace_id="ws-1",
        phase="writing",
        expected_generation=expected,
    )


def _record_intent(home, run_id, intent_id, workflow, generation, version):
    run_dir = home / "runs" / run_id
    (run_dir / ".dsh-workbench").mkdir(parents=True, exist_ok=True)
    p = run_dir / ".dsh-workbench" / "intents.json"
    items = []
    if p.exists():
        items = json.loads(p.read_text(encoding="utf-8")).get("intents", [])
    items.append({
        "intentId": intent_id, "runId": run_id, "workflowId": workflow,
        "generation": generation, "articleVersion": version, "expiresAt": "", "revoked": False,
    })
    p.write_text(json.dumps({"intents": items}, ensure_ascii=False), encoding="utf-8")


def test_commit_write_failure_leaves_old_generation_and_intents(monkeypatch, seeded_home):
    home, run_id, version = seeded_home
    assert _publish(run_id, 1, workflow="wf-old") is not None
    _record_intent(home, run_id, "intent-old", "wf-old", 1, version)

    article_before = (home / "runs" / run_id / "article.md").read_bytes()
    state_before = (home / "runs" / run_id / "state.yaml").read_bytes()

    # 注入绑定 commit 写盘失败（单一写点失败即整事务失败，无半提交）。
    original = storage.write_run_file_rel

    def failing_write(run_id_arg, relpath, data):
        if relpath == ".dsh-workbench/binding.json":
            raise OSError("injected commit write failure")
        return original(run_id_arg, relpath, data)

    monkeypatch.setattr(storage, "write_run_file_rel", failing_write)

    with pytest.raises(OSError):
        _publish(run_id, 2, workflow="wf-new", expected=1)

    # binding 仍为旧代次、旧 intent 未撤销、正文/状态字节不变。
    persisted = binding_mod.read_binding(run_id)
    assert persisted["generation"] == 1, persisted
    intents = intents_mod.read_intents(run_id)
    assert any(i["intentId"] == "intent-old" and i["revoked"] is False for i in intents)
    assert (home / "runs" / run_id / "article.md").read_bytes() == article_before
    assert (home / "runs" / run_id / "state.yaml").read_bytes() == state_before


def test_commit_replays_revocations_on_read(seeded_home):
    """进程在写 commit 后、重放撤销前退出：下次 read_binding 幂等重放撤销。"""
    home, run_id, version = seeded_home
    assert _publish(run_id, 1, workflow="wf-old") is not None
    _record_intent(home, run_id, "intent-old", "wf-old", 1, version)

    # 接管成功，binding 内嵌 revokedIntentIds；手动把 intents 里 revoked 复位模拟重放未执行。
    assert _publish(run_id, 2, workflow="wf-new", expected=1) is not None
    intents = intents_mod.read_intents(run_id)
    for item in intents:
        if item["intentId"] == "intent-old":
            item["revoked"] = False
    p = home / "runs" / run_id / ".dsh-workbench" / "intents.json"
    p.write_text(json.dumps({"intents": intents}, ensure_ascii=False), encoding="utf-8")

    # 再次 read_binding 触发幂等重放。
    binding_mod.read_binding(run_id)
    intents2 = intents_mod.read_intents(run_id)
    assert any(i["intentId"] == "intent-old" and i["revoked"] is True for i in intents2)
