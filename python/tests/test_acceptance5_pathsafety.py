"""验收 5：路径安全用例全过——无越界读写，种子文件哈希不变，拒绝信息不泄露绝对敏感路径。

覆盖：`../`、编码穿越（%2e%2e%2f、双重编码）、反斜线、state 路径伪造、真实跨 run
登记项伪造、symlink 置换。伪造 state.artifacts 后必须证明读到固定文件（不允许请求
失败即算通过）。
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import struct
import zlib
from pathlib import Path

import yaml

from conftest import call_facade, create_run, make_home, write_article


def _seed(home: Path, run_id: str, content: str = "secret-seed") -> str:
    return write_article(home, run_id, content)


def _assert_seed_unchanged(home: Path, run_id: str, content: str = "secret-seed"):
    data = (home / "runs" / run_id / "article.md").read_bytes()
    assert hashlib.sha256(data).hexdigest() == hashlib.sha256(content.encode()).hexdigest()


def _assert_no_abs_path(resp):
    text = json.dumps(resp, ensure_ascii=False)
    # 拒绝信息不得泄露绝对敏感路径（含 home 路径与文件系统根目录提示）。
    home = os.path.expanduser("~")
    assert home not in text
    assert "Traceback" not in text


def test_reject_parent_traversal(tmp_path):
    home = make_home(tmp_path)
    run_id = create_run(home)
    _seed(home, run_id)
    resp = call_facade(str(home), "run.detail", {"runId": "../" + run_id})
    assert resp["ok"] is False
    assert resp["error"]["code"] == "INVALID_INPUT"
    _assert_seed_unchanged(home, run_id)
    _assert_no_abs_path(resp)


def test_reject_absolute_path(tmp_path):
    home = make_home(tmp_path)
    run_id = create_run(home)
    _seed(home, run_id)
    resp = call_facade(str(home), "artifact.get", {"runId": run_id, "kind": "/etc/passwd"})
    assert resp["ok"] is False
    _assert_seed_unchanged(home, run_id)
    _assert_no_abs_path(resp)


def test_reject_backslash(tmp_path):
    home = make_home(tmp_path)
    run_id = create_run(home)
    _seed(home, run_id)
    resp = call_facade(str(home), "run.detail", {"runId": run_id + "\\..\\.."})
    assert resp["ok"] is False
    _assert_seed_unchanged(home, run_id)


def test_reject_encoded_traversal(tmp_path):
    """编码穿越：%2e%2e%2f 与双重编码 %252e%252e%252f 均被拒绝。"""
    home = make_home(tmp_path)
    run_id = create_run(home)
    _seed(home, run_id)
    for evil in ("%2e%2e%2f" + run_id, "%252e%252e%252f" + run_id):
        resp = call_facade(str(home), "run.detail", {"runId": evil})
        assert resp["ok"] is False, f"{evil} 应被拒绝"
        assert resp["error"]["code"] == "INVALID_INPUT"
    _assert_seed_unchanged(home, run_id)


def _png_bytes() -> bytes:
    sig = b"\x89PNG\r\n\x1a\n"

    def chunk(t: bytes, d: bytes) -> bytes:
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00\xff\x00\x00")
    return sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


def test_reject_cross_run_registry_forgery(tmp_path):
    """真实跨 run 登记项伪造：run_b 的 manifest 登记指向 run_a 的资产路径，必须被拒。"""
    home = make_home(tmp_path)
    run_a = create_run(home, "A")
    run_b = create_run(home, "B")
    _seed(home, run_a, "seed-a")

    # 在 run_a 里放一个合法资产文件与登记项。
    asset_a_dir = home / "runs" / run_a / "assets"
    asset_a_dir.mkdir(parents=True, exist_ok=True)
    png = _png_bytes()
    h = hashlib.sha256(png).hexdigest()
    (asset_a_dir / f"{h}.png").write_bytes(png)
    (asset_a_dir / "manifest.json").write_text(
        json.dumps({"assets": [{"assetId": h[:32], "role": "cover", "mediaType": "image/png",
                                "contentHash": h, "relpath": f"assets/{h}.png"}]}, ensure_ascii=False),
        encoding="utf-8",
    )

    # run_b 的 manifest 伪造 relpath 指向 run_a 的资产（跨 run 引用）。
    asset_b_dir = home / "runs" / run_b / "assets"
    asset_b_dir.mkdir(parents=True, exist_ok=True)
    (asset_b_dir / "manifest.json").write_text(
        json.dumps({"assets": [{"assetId": h[:32], "role": "cover", "mediaType": "image/png",
                                "contentHash": h, "relpath": f"../../{run_a}/assets/{h}.png"}]}, ensure_ascii=False),
        encoding="utf-8",
    )

    resp = call_facade(str(home), "asset.get", {"runId": run_b, "assetId": h[:32]})
    assert resp["ok"] is False
    # 种子文件（run_a 的 article.md）哈希不变。
    data = (home / "runs" / run_a / "article.md").read_bytes()
    assert hashlib.sha256(data).hexdigest() == hashlib.sha256("seed-a".encode()).hexdigest()
    _assert_no_abs_path(resp)


def test_state_artifacts_forgery_reads_fixed_file(tmp_path):
    """伪造 state.artifacts 后必须证明读到固定文件（不允许请求失败即算通过）。"""
    home = make_home(tmp_path)
    run_id = create_run(home)
    _seed(home, run_id, "secret-seed")

    run_dir = home / "runs" / run_id
    outside = home / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    state = yaml.safe_load((run_dir / "state.yaml").read_text(encoding="utf-8"))
    state["artifacts"] = {"article": "../outside.md"}
    (run_dir / "state.yaml").write_text(yaml.safe_dump(state, allow_unicode=True), encoding="utf-8")

    resp = call_facade(str(home), "artifact.get", {"runId": run_id, "kind": "article"})
    # 固定文件名映射，必须成功读到固定文件 article.md 的种子内容，而不是伪造的 outside。
    assert resp["ok"] is True, resp
    assert resp["data"]["content"] == "secret-seed"
    _assert_seed_unchanged(home, run_id)


def test_reject_symlink_swap(tmp_path):
    home = make_home(tmp_path)
    run_id = create_run(home)
    _seed(home, run_id)
    run_dir = home / "runs" / run_id
    outside = home / "outside.md"
    outside.write_text("attacker", encoding="utf-8")
    os.unlink(run_dir / "article.md")
    os.symlink(outside, run_dir / "article.md")
    resp = call_facade(str(home), "artifact.get", {"runId": run_id, "kind": "article"})
    assert resp["ok"] is False
    _assert_no_abs_path(resp)
