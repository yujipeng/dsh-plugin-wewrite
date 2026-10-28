"""第 4 轮回归：不可读 state 的写面守卫——所有写入该 run 的入口在持锁后校验
原始 state.yaml，损坏即拒绝（STATE_CORRUPTED），state.yaml 与产物字节均不变，
run.detail.lastError 仍为阻断。"""

from __future__ import annotations

import base64
import hashlib
import json
import struct
import zlib
from pathlib import Path

import pytest

from conftest import call_facade, create_run, make_home, write_article

CORRUPT = b"broken: [unclosed\n"
META = {"title": "t", "digest": "d", "themeId": "professional-clean"}


def _png_bytes() -> bytes:
    sig = b"\x89PNG\r\n\x1a\n"

    def chunk(t: bytes, d: bytes) -> bytes:
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00\xff\x00\x00")
    return sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


def _corrupt_state(home: Path, run_id: str) -> Path:
    run_dir = home / "runs" / run_id
    (run_dir / "state.yaml").write_bytes(CORRUPT)
    return run_dir


def _state_bytes(run_dir: Path) -> bytes:
    return (run_dir / "state.yaml").read_bytes()


def _assert_guard(resp: dict, run_dir: Path, article_before: bytes | None = None):
    assert resp["ok"] is False, resp
    assert resp["error"]["code"] == "STATE_CORRUPTED", resp
    assert _state_bytes(run_dir) == CORRUPT, "state.yaml 不得被覆盖"
    if article_before is not None:
        assert (run_dir / "article.md").read_bytes() == article_before, "产物字节不得被改写"


def _seed(home: Path) -> tuple[str, str, Path]:
    """run.create + article.save 后把 state.yaml 写坏，返回 (run_id, version, run_dir)。"""
    run_id = create_run(home)
    ver = write_article(home, run_id, "种子正文")
    run_dir = _corrupt_state(home, run_id)
    return run_id, ver, run_dir


def test_article_save_rejected(tmp_path):
    home = make_home(tmp_path)
    run_id, ver, run_dir = _seed(home)
    article_before = (run_dir / "article.md").read_bytes()
    resp = call_facade(str(home), "article.save", {
        "requestId": "w", "runId": run_id, "markdown": "覆盖后正文",
        "expectedVersion": ver, "metadata": META,
    })
    _assert_guard(resp, run_dir, article_before)
    detail = call_facade(str(home), "run.detail", {"runId": run_id})
    assert detail["data"]["lastError"]["code"] == "STATE_CORRUPTED"
    assert detail["data"]["lastError"]["blocking"] is True


def test_artifact_write_rejected(tmp_path):
    home = make_home(tmp_path)
    run_id, ver, run_dir = _seed(home)
    article_before = (run_dir / "article.md").read_bytes()
    resp = call_facade(str(home), "artifact.write", {
        "requestId": "w", "runId": run_id, "kind": "article",
        "content": "覆盖后正文", "expectedVersion": ver,
    })
    _assert_guard(resp, run_dir, article_before)


def test_asset_put_rejected(tmp_path):
    home = make_home(tmp_path)
    run_id, ver, run_dir = _seed(home)
    article_before = (run_dir / "article.md").read_bytes()
    b64 = base64.b64encode(_png_bytes()).decode("ascii")
    resp = call_facade(str(home), "asset.put", {
        "requestId": "w", "runId": run_id, "role": "cover",
        "mediaType": "image/png", "base64": b64,
    })
    _assert_guard(resp, run_dir, article_before)
    # 不得落盘资产目录。
    assert not (run_dir / "assets").exists()


def test_draft_prepare_rejected(tmp_path):
    home = make_home(tmp_path)
    run_id, ver, run_dir = _seed(home)
    article_before = (run_dir / "article.md").read_bytes()
    resp = call_facade(str(home), "draft.prepare", {
        "requestId": "w", "runId": run_id, "expectedVersion": None,
        "coverAssetId": "c", "themeId": "professional-clean",
    })
    # draft.prepare 在写 intent 前先读正文（只读可过），随后写面守卫拦截。
    assert resp["ok"] is False, resp
    assert resp["error"]["code"] == "STATE_CORRUPTED", resp
    assert _state_bytes(run_dir) == CORRUPT
    assert not (run_dir / ".dsh-workbench").exists()


def test_review_build_rejected(tmp_path):
    home = make_home(tmp_path)
    run_id, ver, run_dir = _seed(home)
    article_before = (run_dir / "article.md").read_bytes()
    resp = call_facade(str(home), "review.build", {
        "requestId": "w", "runId": run_id, "articleVersion": None, "draftVersion": None,
        "assessment": {"decision": "pass", "pass_number": 1,
                       "dimensions": {"accuracy": 5, "viewpoint": 5, "usefulness": 5, "voice": 5, "readability": 5},
                       "blockers": [], "major_issues": [], "notes": "ok"},
    })
    assert resp["ok"] is False, resp
    assert resp["error"]["code"] == "STATE_CORRUPTED", resp
    assert _state_bytes(run_dir) == CORRUPT
    assert not (run_dir / "review-report.json").exists()


def test_run_finish_rejected(tmp_path):
    home = make_home(tmp_path)
    run_id, ver, run_dir = _seed(home)
    article_before = (run_dir / "article.md").read_bytes()
    resp = call_facade(str(home), "run.finish", {
        "requestId": "w", "runId": run_id,
    })
    assert resp["ok"] is False, resp
    assert resp["error"]["code"] == "STATE_CORRUPTED", resp
    assert _state_bytes(run_dir) == CORRUPT
    assert (run_dir / "article.md").read_bytes() == article_before


def test_run_step_rejected(tmp_path):
    home = make_home(tmp_path)
    run_id, ver, run_dir = _seed(home)
    article_before = (run_dir / "article.md").read_bytes()
    resp = call_facade(str(home), "run.step", {
        "requestId": "w", "runId": run_id, "step": "topic", "status": "completed", "error": None,
    })
    assert resp["ok"] is False, resp
    assert resp["error"]["code"] == "STATE_CORRUPTED", resp
    assert _state_bytes(run_dir) == CORRUPT
    assert (run_dir / "article.md").read_bytes() == article_before


def test_run_fork_rejected(tmp_path):
    home = make_home(tmp_path)
    run_id, ver, run_dir = _seed(home)
    article_before = (run_dir / "article.md").read_bytes()
    resp = call_facade(str(home), "run.fork", {
        "requestId": "w", "runId": run_id, "expectedVersion": ver,
    })
    assert resp["ok"] is False, resp
    assert resp["error"]["code"] == "STATE_CORRUPTED", resp
    assert _state_bytes(run_dir) == CORRUPT
    assert (run_dir / "article.md").read_bytes() == article_before
