"""验收 5：路径安全用例全过——无越界读写，种子文件哈希不变，拒绝信息不泄露绝对敏感路径。"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from conftest import call_facade, create_run, make_home, write_article


def _seed(home: Path, run_id: str, content: str = "secret-seed") -> str:
    v = write_article(home, run_id, content)
    return v


def _assert_seed_unchanged(home: Path, run_id: str, content: str = "secret-seed"):
    data = (home / "runs" / run_id / "article.md").read_bytes()
    assert hashlib.sha256(data).hexdigest() == hashlib.sha256(content.encode()).hexdigest()


def test_reject_parent_traversal(tmp_path):
    home = make_home(tmp_path)
    run_id = create_run(home)
    _seed(home, run_id)
    resp = call_facade(str(home), "run.detail", {"runId": "../" + run_id})
    assert resp["ok"] is False
    assert resp["error"]["code"] == "INVALID_INPUT"
    _assert_seed_unchanged(home, run_id)


def test_reject_absolute_path(tmp_path):
    home = make_home(tmp_path)
    run_id = create_run(home)
    _seed(home, run_id)
    resp = call_facade(str(home), "artifact.get", {"runId": run_id, "kind": "/etc/passwd"})
    assert resp["ok"] is False
    _assert_seed_unchanged(home, run_id)


def test_reject_backslash(tmp_path):
    home = make_home(tmp_path)
    run_id = create_run(home)
    _seed(home, run_id)
    resp = call_facade(str(home), "run.detail", {"runId": run_id + "\\..\\.."})
    assert resp["ok"] is False
    _assert_seed_unchanged(home, run_id)


def test_reject_cross_run_reference(tmp_path):
    home = make_home(tmp_path)
    run_a = create_run(home, "A")
    run_b = create_run(home, "B")
    _seed(home, run_a)
    # 用 B 的 runId 请求 A 的产物？契约按 runId 定位目录；跨 run 引用用 asset.get 的 relpath 伪造。
    resp = call_facade(str(home), "asset.get", {"runId": run_b, "assetId": ".."})
    assert resp["ok"] is False
    _assert_seed_unchanged(home, run_a)


def test_reject_state_artifacts_forgery(tmp_path):
    home = make_home(tmp_path)
    run_id = create_run(home)
    _seed(home, run_id)
    # 在 state.yaml 里伪造 artifacts.article 指向 run 外文件；facade 必须不信任该字段。
    run_dir = home / "runs" / run_id
    outside = home / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    import yaml
    state = yaml.safe_load((run_dir / "state.yaml").read_text(encoding="utf-8"))
    state["artifacts"] = {"article": "../outside.md"}
    (run_dir / "state.yaml").write_text(yaml.safe_dump(state, allow_unicode=True), encoding="utf-8")
    resp = call_facade(str(home), "artifact.get", {"runId": run_id, "kind": "article"})
    # 固定文件名映射，不读伪造路径；article.md 仍是种子。
    if resp["ok"] is True:
        assert resp["data"]["content"] == "secret-seed"
    _assert_seed_unchanged(home, run_id)


def test_reject_symlink_swap(tmp_path):
    home = make_home(tmp_path)
    run_id = create_run(home)
    _seed(home, run_id)
    run_dir = home / "runs" / run_id
    outside = home / "outside.md"
    outside.write_text("attacker", encoding="utf-8")
    # 用 symlink 替换 article.md。
    os.unlink(run_dir / "article.md")
    os.symlink(outside, run_dir / "article.md")
    resp = call_facade(str(home), "artifact.get", {"runId": run_id, "kind": "article"})
    assert resp["ok"] is False
    # 拒绝信息不泄露绝对路径。
    err = json_dumps_no_path(resp)
    assert str(home) not in err


def test_reject_message_no_abs_path(tmp_path):
    home = make_home(tmp_path)
    run_id = create_run(home)
    _seed(home, run_id)
    resp = call_facade(str(home), "artifact.get", {"runId": run_id, "kind": "/etc/passwd"})
    assert resp["ok"] is False
    assert str(home) not in json_dumps_no_path(resp)


def json_dumps_no_path(resp):
    import json
    return json.dumps(resp, ensure_ascii=False)
