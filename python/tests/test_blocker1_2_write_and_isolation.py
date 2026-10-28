"""阻断 1：写路径可用——run.create 后 draft.prepare、asset.put 端到端成功。
阻断 2：事务隔离——正文更新后旧审稿未删、journal 未提交，读面不返回 pass。
"""

from __future__ import annotations

import base64
import hashlib
import json
import struct
import zlib
from pathlib import Path

import yaml

from conftest import call_facade, create_run, make_home, write_article


def _png_bytes() -> bytes:
    sig = b"\x89PNG\r\n\x1a\n"

    def chunk(t: bytes, d: bytes) -> bytes:
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00\xff\x00\x00")
    return sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


def test_draft_prepare_end_to_end_success(tmp_path):
    home = make_home(tmp_path)
    run_id = create_run(home)
    # 先写正文，draft.prepare 才能读到 article 并生成 intent。
    write_article(home, run_id, "# 标题\n\n正文")
    resp = call_facade(str(home), "draft.prepare", {
        "requestId": "p1",
        "runId": run_id,
        "expectedVersion": None,
        "coverAssetId": "cover-1",
        "themeId": "professional-clean",
    })
    assert resp["ok"] is True, resp
    assert resp["data"]["intentId"]
    # intent.json 实际落盘（写面如实）。
    intent = home / "runs" / run_id / ".dsh-workbench" / "intent.json"
    assert intent.is_file()


def test_asset_put_end_to_end_success(tmp_path):
    home = make_home(tmp_path)
    run_id = create_run(home)
    b64 = base64.b64encode(_png_bytes()).decode("ascii")
    resp = call_facade(str(home), "asset.put", {
        "requestId": "a1",
        "runId": run_id,
        "role": "cover",
        "mediaType": "image/png",
        "base64": b64,
        "expectedArticleVersion": None,
    })
    assert resp["ok"] is True, resp
    data = resp["data"]
    assert data["assetId"]
    assert data["contentHash"]
    assert data["bytes"] == len(_png_bytes())
    # 资产文件与登记项均落盘。
    asset_dir = home / "runs" / run_id / "assets"
    manifest = asset_dir / "manifest.json"
    assert manifest.is_file()
    assert (asset_dir / f"{data['contentHash']}.png").is_file()


def test_pending_transaction_read_face_no_pass(tmp_path):
    """正文已更新、旧审稿未删、journal 未提交 → 读面不返回 pass。"""
    home = make_home(tmp_path)
    run_id = create_run(home)
    write_article(home, run_id, "旧正文\n")

    # 构造一个「审稿通过」的报告，绑定旧正文哈希。
    import subprocess
    old_version = hashlib.sha256("旧正文\n".encode()).hexdigest()
    report = {
        "version": 1,
        "decision": "pass",
        "pass_number": 1,
        "publishable": True,
        "dimensions": {"accuracy": 5, "viewpoint": 5, "usefulness": 5, "voice": 5, "readability": 5},
        "dimension_average": 5.0,
        "blockers": [],
        "major_issues": [],
        "notes": "",
        "draft_chars": 3,
        "final_chars": 3,
        "edit_ratio": 0.0,
        "structure_changed": False,
        "article_hash": old_version,
    }
    (home / "runs" / run_id / "review-report.json").write_text(
        json.dumps(report, ensure_ascii=False), encoding="utf-8"
    )

    # 更新正文（用 article.save，会 invalidation 并写 journal），但这里模拟中断：
    # 直接在 journal 里留 pending 且不删除旧审稿、不提交。
    journal_dir = home / "runs" / run_id / ".dsh-workbench"
    journal_dir.mkdir(parents=True, exist_ok=True)
    (journal_dir / "journal.json").write_text(
        json.dumps({"pending": {"id": "txn", "kind": "article_save", "expected": old_version}}, ensure_ascii=False),
        encoding="utf-8",
    )

    # 读面 run.detail：pending 事务 → 业务失败，不展示 pass。
    detail = call_facade(str(home), "run.detail", {"runId": run_id})
    assert detail["ok"] is False
    assert detail["error"]["code"] == "VERSION_CONFLICT"

    # 即使没有 pending，正文哈希与审稿绑定哈希不一致也不展示 pass。
    (journal_dir / "journal.json").unlink()
    new_version = write_article(home, run_id, "新正文已更新\n")
    gate_resp = call_facade(str(home), "run.detail", {"runId": run_id})
    assert gate_resp["ok"] is True
    gate = gate_resp["data"]["gate"]
    assert gate is None or gate["publishable"] is False
