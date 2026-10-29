"""S3 接管栅栏（facade 侧）：binding.publish CAS、旧 generation 零写、并发接管仅一个成功。

设计 §5 接管栅栏落地在 facade 的验证面：
- binding.publish 首次 generation=1；expectedGeneration 不匹配（含并发接管）VERSION_CONFLICT。
- 写入携带 context 时复核 workflowId/generation：旧 generation 一律 CANCELLED，正文/报告/状态字节不变。
- 不携带 context 的 RPC owner 编辑路径（article.save 等）不受绑定栅栏约束（编辑走 article/fork 派生）。
"""

from __future__ import annotations

from pathlib import Path

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


def _ctx(workflow="wf-a", generation=1):
    return {"runId": None, "workflowId": workflow, "generation": generation}


def test_binding_publish_first_then_takeover(tmp_path):
    home = make_home(tmp_path)
    run_id = create_run(home)
    ver = write_article(home, run_id, "种子正文")

    r1 = _publish(home, run_id, 1)
    assert r1["ok"] is True, r1
    assert r1["data"]["generation"] == 1

    # 无 expected 的二次发布被拒（并发/重复首次绑定 CAS 失败）。
    r2 = _publish(home, run_id, 1, workflow="wf-b")
    assert r2["ok"] is False and r2["error"]["code"] == "VERSION_CONFLICT", r2

    # 接管：expectedGeneration=1 → generation=2 成功（新 workflowId）。
    r3 = _publish(home, run_id, 2, workflow="wf-b", expected=1)
    assert r3["ok"] is True, r3
    assert r3["data"]["generation"] == 2
    assert r3["data"]["workflowId"] == "wf-b"

    # 旧工作流的旧 generation 写入被拒：FORBIDDEN（跨工作流），正文字节不变。
    before = (home / "runs" / run_id / "article.md").read_bytes()
    ctx = _ctx("wf-a", 1)
    ctx["runId"] = run_id
    w = call_facade(str(home), "artifact.write", {
        "requestId": "w", "runId": run_id, "kind": "article",
        "content": "旧租约写入", "expectedVersion": ver, "context": ctx,
    })
    assert w["ok"] is False, w
    assert w["error"]["code"] == "FORBIDDEN", w
    assert (home / "runs" / run_id / "article.md").read_bytes() == before

    # 新 generation=2 正常写入。
    ctx2 = _ctx("wf-b", 2)
    ctx2["runId"] = run_id
    w2 = call_facade(str(home), "artifact.write", {
        "requestId": "w2", "runId": run_id, "kind": "article",
        "content": "新租约写入", "expectedVersion": ver, "context": ctx2,
    })
    assert w2["ok"] is True, w2


def test_stale_generation_same_workflow_cancelled(tmp_path):
    """同 workflowId 但 generation 落后（防御分支）：CANCELLED 且正文/报告字节不变。"""
    home = make_home(tmp_path)
    run_id = create_run(home)
    ver = write_article(home, run_id, "种子正文")
    assert _publish(home, run_id, 1, workflow="wf-a")["ok"] is True
    # 同 workflow 的 generation 递增（防御性接管）：expected=1 → 2。
    assert _publish(home, run_id, 2, workflow="wf-a", expected=1)["ok"] is True

    before = (home / "runs" / run_id / "article.md").read_bytes()
    ctx = _ctx("wf-a", 1)
    ctx["runId"] = run_id
    w = call_facade(str(home), "artifact.write", {
        "requestId": "w", "runId": run_id, "kind": "article",
        "content": "旧 generation 写入", "expectedVersion": ver, "context": ctx,
    })
    assert w["ok"] is False, w
    assert w["error"]["code"] == "CANCELLED", w
    assert (home / "runs" / run_id / "article.md").read_bytes() == before


def test_context_runid_mismatch_rejected(tmp_path):
    home = make_home(tmp_path)
    run_id = create_run(home)
    _publish(home, run_id, 1)
    ctx = _ctx("wf-a", 1)
    ctx["runId"] = "20261028-120000-abcdee"  # 另一个合法 runId
    w = call_facade(str(home), "artifact.write", {
        "requestId": "w", "runId": run_id, "kind": "article",
        "content": "x", "expectedVersion": None, "context": ctx,
    })
    assert w["ok"] is False and w["error"]["code"] == "INVALID_INPUT", w


def test_concurrent_takeover_only_one_wins(tmp_path):
    home = make_home(tmp_path)
    run_id = create_run(home)
    assert _publish(home, run_id, 1)["ok"] is True
    # 两个并发接管（都从 generation=1 出发）仅一个成功。
    a = _publish(home, run_id, 2, workflow="wf-a", expected=1)
    b = _publish(home, run_id, 2, workflow="wf-b", expected=1)
    assert sorted([a["ok"], b["ok"]]) == [False, True]
    ok = a if a["ok"] is True else b
    assert ok["data"]["generation"] == 2


def test_unbound_run_rejects_context_write(tmp_path):
    home = make_home(tmp_path)
    run_id = create_run(home)
    ver = write_article(home, run_id, "正文")
    ctx = _ctx("wf-a", 1)
    ctx["runId"] = run_id
    w = call_facade(str(home), "artifact.write", {
        "requestId": "w", "runId": run_id, "kind": "article",
        "content": "x", "expectedVersion": ver, "context": ctx,
    })
    assert w["ok"] is False and w["error"]["code"] == "FORBIDDEN", w


def test_owner_edit_without_context_not_fenced(tmp_path):
    """RPC 编辑（article.save）不带 context，走 article/fork 派生，不受绑定栅栏约束。"""
    home = make_home(tmp_path)
    run_id = create_run(home)
    ver = write_article(home, run_id, "原文")
    r = call_facade(str(home), "article.save", {
        "requestId": "e", "runId": run_id, "markdown": "编辑后",
        "expectedVersion": ver, "metadata": {"title": "t", "digest": "d", "themeId": "professional-clean"},
    })
    assert r["ok"] is True, r
    assert r["data"]["version"] != ver
