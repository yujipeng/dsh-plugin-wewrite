"""验收 2：读面无写盘。

对 v4 最新格式、旧格式、损坏 state 三种，连续 snapshot/detail/artifact.get 后，
目录内所有既有文件字节与 mtime 均不变，无新增目录/锁文件（GET 不创建锁设施）。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import yaml

from conftest import call_facade, create_run, make_home, snapshot_tree, write_article


def _seed_v4(home: Path, run_id: str):
    run_dir = home / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    state = {
        "version": 4,
        "run_id": run_id,
        "created": "2026-10-28T12:00:00+00:00",
        "updated": "2026-10-28T12:00:00+00:00",
        "status": "active",
        "mode": "draft",
        "permissions": {"publish": False},
        "visual": {"mode": "none", "max_images": 4, "max_cost": None},
        "topic": {"title": "标题", "keywords": [], "source": "", "framework_hint": ""},
        "artifacts": {},
        "steps": {},
        "last_error": None,
    }
    (run_dir / "state.yaml").write_text(yaml.safe_dump(state, allow_unicode=True, sort_keys=False), encoding="utf-8")
    (run_dir / "article.md").write_text("# 正文\n\n内容", encoding="utf-8")


def _seed_legacy(home: Path, run_id: str):
    run_dir = home / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    state = {
        "version": 1,
        "run_id": run_id,
        "created": "2026-10-28T12:00:00+00:00",
        "status": "active",
        "mode": "draft",
        "topic": {"title": "旧格式"},
    }
    (run_dir / "state.yaml").write_text(yaml.safe_dump(state, allow_unicode=True, sort_keys=False), encoding="utf-8")
    (run_dir / "article.md").write_text("旧正文", encoding="utf-8")


def _seed_corrupt(home: Path, run_id: str):
    run_dir = home / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "state.yaml").write_text(":\n  - not: [valid", encoding="utf-8")


def _read_then_assert_unchanged(home: Path, run_id: str, before: dict):
    call_facade(str(home), "snapshot", {})
    call_facade(str(home), "run.detail", {"runId": run_id})
    call_facade(str(home), "artifact.get", {"runId": run_id, "kind": "article"})
    after = snapshot_tree(home)
    assert after == before, f"读面写盘：before={before}\nafter={after}"


def test_read_face_no_write_v4(tmp_path):
    home = make_home(tmp_path)
    run_id = create_run(home)
    _seed_v4(home, run_id)
    before = snapshot_tree(home)
    _read_then_assert_unchanged(home, run_id, before)


def test_read_face_no_write_legacy(tmp_path):
    home = make_home(tmp_path)
    run_id = create_run(home)
    _seed_legacy(home, run_id)
    before = snapshot_tree(home)
    _read_then_assert_unchanged(home, run_id, before)


@pytest.mark.parametrize("raw_state", [b":\n  - not: [valid", b"- not-a-mapping", b"\xff\xfe"])
def test_read_face_no_write_corrupt(tmp_path, raw_state):
    home = make_home(tmp_path)
    run_id = create_run(home)
    _seed_corrupt(home, run_id)
    run_dir = home / "runs" / run_id
    (run_dir / "state.yaml").write_bytes(raw_state)
    article_hash = write_article(home, run_id, "已有正文")
    (run_dir / "review-report.json").write_text(json.dumps({
        "publishable": True, "article_hash": article_hash,
    }), encoding="utf-8")
    before = snapshot_tree(home)
    dirs_before = sorted(str(p.relative_to(home)) for p in home.rglob("*") if p.is_dir())
    # 损坏 state 显示不可读而非消失；连续执行三种读，且返回「不可读」投影而非崩溃。
    resp = call_facade(str(home), "snapshot", {})
    assert resp["ok"] is True
    runs = [r for r in resp["data"]["runs"] if r["runId"] == run_id]
    assert len(runs) == 1
    summary = runs[0]
    assert summary["title"] == "状态不可读"
    assert summary["pythonStatus"] == "unreadable"
    assert summary["phase"] == "failed"
    assert summary["editable"] is False
    assert {"code": "STATE_CORRUPTED", "message": "状态不可读", "blocking": True} in resp["data"]["diagnostics"]
    detail = call_facade(str(home), "run.detail", {"runId": run_id})
    assert detail["ok"] is True
    assert detail["data"]["run"] == summary
    assert detail["data"]["lastError"]["code"] == "STATE_CORRUPTED"
    assert detail["data"]["lastError"]["blocking"] is True
    assert detail["data"]["steps"] == []
    assert detail["data"]["artifacts"] == []
    assert detail["data"]["gate"] is None
    art = call_facade(str(home), "artifact.get", {"runId": run_id, "kind": "article"})
    assert art["ok"] is False
    assert art["error"]["code"] == "INVALID_TRANSITION"
    assert art["error"]["runId"] == run_id
    after = snapshot_tree(home)
    assert after == before
    assert sorted(str(p.relative_to(home)) for p in home.rglob("*") if p.is_dir()) == dirs_before


def test_get_does_not_create_lock(tmp_path):
    home = make_home(tmp_path)
    run_id = create_run(home)
    _seed_v4(home, run_id)
    # 删除锁文件（模拟仅初始化无锁设施），GET 不得重新创建。
    lock = home / ".dsh-workbench.lock"
    if lock.exists():
        lock.unlink()
    call_facade(str(home), "run.detail", {"runId": run_id})
    assert not lock.exists(), "GET 不应创建锁文件"


def test_read_face_missing_home_returns_diagnostic(tmp_path):
    home = tmp_path / "nonexistent"
    resp = call_facade(str(home), "snapshot", {})
    assert resp["ok"] is True
    diags = resp["data"]["diagnostics"]
    assert any(d["code"] == "HOME_UNINITIALIZED" for d in diags)
    # 未创建任何目录。
    assert not home.exists()
