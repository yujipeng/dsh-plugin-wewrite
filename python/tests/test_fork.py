"""第 5 轮回归：run.fork 健康路径成功 + 失败零残留（孤儿目录 / current_run 指针）。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from conftest import call_facade, create_run, make_home, snapshot_tree, write_article


def _seed_healthy(home: Path) -> tuple[str, str]:
    run_id = create_run(home, "父")
    ver = write_article(home, run_id, "父正文")
    return run_id, ver


def test_fork_healthy_path_success(tmp_path):
    home = make_home(tmp_path)
    run_id, ver = _seed_healthy(home)
    parent_before = snapshot_tree(home / "runs" / run_id)
    pointer_before = (home / "current_run").read_bytes()

    resp = call_facade(str(home), "run.fork", {
        "requestId": "f1", "runId": run_id, "expectedVersion": ver,
    })
    assert resp["ok"] is True, resp
    data = resp["data"]
    # 契约 article/fork 的 response 三字段，无多余字段。
    assert set(data.keys()) == {"runId", "parentRunId", "version"}
    assert data["parentRunId"] == run_id
    assert data["runId"] != run_id
    assert data["version"] == ver

    new_run_id = data["runId"]
    new_run_dir = home / "runs" / new_run_id
    assert new_run_dir.is_dir()
    # 新 run 锁文件已预建（后续 create=False 取得 run 锁）。
    assert (new_run_dir / ".dsh-workbench.lock").is_file()
    # 正文已复制。
    assert (new_run_dir / "article.md").read_text(encoding="utf-8") == "父正文"
    # parentRunId / fork_source_version 落盘。
    state = yaml.safe_load((new_run_dir / "state.yaml").read_text(encoding="utf-8"))
    assert state.get("parent_run_id") == run_id
    assert state.get("fork_source_version") == ver

    # 源 run 正文与 state 字节不变。
    src_article = (home / "runs" / run_id / "article.md").read_bytes()
    assert snapshot_tree(home / "runs" / run_id) == parent_before
    assert (home / "current_run").read_bytes() == pointer_before
    assert src_article == b"\xe7\x88\xb6\xe6\xad\xa3\xe6\x96\x87"  # "父正文"


def test_fork_failure_zero_residue(tmp_path):
    """健康 run 上 fork 因正文版本不匹配失败：runs/ 集合不变、current_run 逐字节不变。"""
    home = make_home(tmp_path)
    run_id, ver = _seed_healthy(home)

    runs_before = sorted(p.name for p in (home / "runs").iterdir())
    ptr_before = (home / "current_run").read_bytes()

    # 用错误版本触发 VERSION_CONFLICT（在写面守卫之后、建新 run 之前）。
    resp = call_facade(str(home), "run.fork", {
        "requestId": "f1", "runId": run_id,
        "expectedVersion": "0" * 64,
    })
    assert resp["ok"] is False, resp
    assert resp["error"]["code"] == "VERSION_CONFLICT", resp

    runs_after = sorted(p.name for p in (home / "runs").iterdir())
    assert runs_after == runs_before, f"fork 失败不应留孤儿 run：{runs_before} -> {runs_after}"
    assert (home / "current_run").read_bytes() == ptr_before, "current_run 指针必须逐字节不变"


def test_fork_failure_corrupt_state_zero_residue(tmp_path):
    """损坏 state 上 fork 被拒：runs/ 集合不变、current_run 逐字节不变。"""
    home = make_home(tmp_path)
    run_id, ver = _seed_healthy(home)
    (home / "runs" / run_id / "state.yaml").write_bytes(b"broken: [unclosed\n")

    runs_before = sorted(p.name for p in (home / "runs").iterdir())
    ptr_before = (home / "current_run").read_bytes()

    resp = call_facade(str(home), "run.fork", {
        "requestId": "f1", "runId": run_id, "expectedVersion": ver,
    })
    assert resp["ok"] is False, resp
    assert resp["error"]["code"] == "STATE_CORRUPTED", resp

    runs_after = sorted(p.name for p in (home / "runs").iterdir())
    assert runs_after == runs_before, f"损坏态 fork 不应留孤儿 run"
    assert (home / "current_run").read_bytes() == ptr_before


# ── 第 6 轮：真正进入回滚分支的并发用例 ────────────────────────────────────

def _seed_inprocess(home: Path) -> tuple[str, str]:
    """子进程 helper 建好 run 后，在测试进程内复用（同 home）。"""
    run_id = create_run(home, "父")
    ver = write_article(home, run_id, "父正文")
    return run_id, ver


def test_fork_rollback_keeps_concurrent_run(tmp_path, monkeypatch):
    """注入「新 run 已创建后」失败，且期间并发建了另一 run：
    回滚只删本次 fork 的 run，并发 run 留存，current_run 不被覆盖。"""
    from dsh_wewrite_facade import storage

    home = make_home(tmp_path)
    rid, ver = _seed_inprocess(home)
    monkeypatch.setenv("WEWRITE_HOME", str(home))

    runs_before = set(p.name for p in (home / "runs").iterdir())
    created: dict[str, str] = {}

    def fake_write(run_id: str, filename: str, data: bytes):
        # 模拟并发 writer（不遵守本 facade 的 home_lock）在此期间建了新 run。
        from wewrite.runs import create_run as _up
        st = _up(topic="并发", mode="draft", visual_mode="none",
                 max_images=4, max_image_cost=None, allow_publish=False)
        created["concurrent"] = st["run_id"]
        created["fork"] = run_id
        raise RuntimeError("注入写正文失败")

    monkeypatch.setattr(storage, "write_run_file", fake_write)

    with pytest.raises(RuntimeError):
        storage.fork_article(rid, ver)

    # 本次 fork 的 run 被清理。
    assert not (home / "runs" / created["fork"]).exists()
    # 并发创建的 run 留存。
    assert (home / "runs" / created["concurrent"]).exists()
    # current_run 仍指向并发 run，未被覆盖回旧父 run。
    assert (home / "current_run").read_text(encoding="utf-8").strip() == created["concurrent"]


def test_fork_initialize_then_raise_zero_residue(tmp_path, monkeypatch):
    """初始化已落盘后抛错：凭据清理本次目录，指针无需恢复。"""
    from dsh_wewrite_facade import storage

    home = make_home(tmp_path)
    rid, ver = _seed_inprocess(home)
    monkeypatch.setenv("WEWRITE_HOME", str(home))

    runs_before = set(p.name for p in (home / "runs").iterdir())
    ptr_before = (home / "current_run").read_bytes()

    real_initialize = storage._initialize_fork_run
    created: dict[str, str] = {}

    def fake_initialize(receipt, parent_id, version):
        real_initialize(receipt, parent_id, version)
        created["run"] = receipt.run_id
        raise RuntimeError("初始化完成后注入失败")

    monkeypatch.setattr(storage, "_initialize_fork_run", fake_initialize)

    with pytest.raises(RuntimeError):
        storage.fork_article(rid, ver)

    # 本次 fork 的 run 被清理。
    assert created["run"] not in set(p.name for p in (home / "runs").iterdir())
    # runs/ 集合与调用前完全相同。
    assert set(p.name for p in (home / "runs").iterdir()) == runs_before
    # current_run 恢复调用前。
    assert (home / "current_run").read_bytes() == ptr_before


# 第 7 轮：独立进程上游 writer 不获取 facade home 锁，确定性同步完成，无睡眠。
def _peer_create(home: Path) -> tuple[str, dict, bytes]:
    env = os.environ.copy()
    env["WEWRITE_HOME"] = str(home)
    peer_id = subprocess.check_output([
        sys.executable, "-c",
        "from wewrite.runs import create_run; print(create_run(topic='并发他人')['run_id'])",
    ], env=env, text=True, timeout=20).strip()
    peer_dir = home / "runs" / peer_id
    (peer_dir / "sentinel.bin").write_bytes(b"peer-owned\x00keep")
    return peer_id, snapshot_tree(peer_dir), (home / "current_run").read_bytes()


def test_fork_receipt_before_state_keeps_peer(tmp_path, monkeypatch):
    """自身尚未落盘、唯一新增 run 属于他人；另覆盖仅分配空目录的异常窗口。"""
    from dsh_wewrite_facade import storage

    home = make_home(tmp_path)
    rid, ver = _seed_inprocess(home)
    monkeypatch.setenv("WEWRITE_HOME", str(home))
    parent_before = snapshot_tree(home / "runs" / rid)
    real_allocate = storage._allocate_fork_run
    peer = {}

    # 精确复现阻断 A：自己完全未 mkdir，唯一新增目录是独立进程建的。
    def fail_before_allocate():
        peer["id"], peer["tree"], peer["pointer"] = _peer_create(home)
        raise RuntimeError("本次尚未落盘")

    monkeypatch.setattr(storage, "_allocate_fork_run", fail_before_allocate)
    with pytest.raises(RuntimeError, match="本次尚未落盘"):
        storage.fork_article(rid, ver)
    assert set(p.name for p in (home / "runs").iterdir()) == {rid, peer["id"]}
    assert snapshot_tree(home / "runs" / peer["id"]) == peer["tree"]
    assert (home / "current_run").read_bytes() == peer["pointer"]

    # 业务 state 未落盘、仅凭据已分配：删除回滚时必须暴露空目录残留。
    monkeypatch.setattr(storage, "_allocate_fork_run", real_allocate)
    runs_before = set(p.name for p in (home / "runs").iterdir())
    mine = {}

    def fail_before_state(receipt, parent_id, version):
        mine["id"] = receipt.run_id
        assert list((home / "runs" / receipt.run_id).iterdir()) == []
        peer["id"], peer["tree"], peer["pointer"] = _peer_create(home)
        raise RuntimeError("本次尚未写状态")

    monkeypatch.setattr(storage, "_initialize_fork_run", fail_before_state)
    with pytest.raises(RuntimeError, match="本次尚未写状态"):
        storage.fork_article(rid, ver)
    assert not (home / "runs" / mine["id"]).exists()
    assert set(p.name for p in (home / "runs").iterdir()) == runs_before | {peer["id"]}
    assert snapshot_tree(home / "runs" / peer["id"]) == peer["tree"]
    assert (home / "current_run").read_bytes() == peer["pointer"]
    assert snapshot_tree(home / "runs" / rid) == parent_before


def test_fork_receipt_after_state_keeps_peer(tmp_path, monkeypatch):
    """自身已落盘且另有并发目录：只删凭据目录，保留他人全部字节与指针。"""
    from dsh_wewrite_facade import storage

    home = make_home(tmp_path)
    rid, ver = _seed_inprocess(home)
    monkeypatch.setenv("WEWRITE_HOME", str(home))
    parent_before = snapshot_tree(home / "runs" / rid)
    runs_before = set(p.name for p in (home / "runs").iterdir())
    real_initialize = storage._initialize_fork_run
    peer = {}
    mine = {}

    def fail_after_state(receipt, parent_id, version):
        real_initialize(receipt, parent_id, version)
        mine["id"] = receipt.run_id
        assert (home / "runs" / receipt.run_id / "state.yaml").is_file()
        peer["id"], peer["tree"], peer["pointer"] = _peer_create(home)
        raise RuntimeError("本次落盘后失败")

    monkeypatch.setattr(storage, "_initialize_fork_run", fail_after_state)
    with pytest.raises(RuntimeError, match="本次落盘后失败"):
        storage.fork_article(rid, ver)
    assert not (home / "runs" / mine["id"]).exists()
    assert set(p.name for p in (home / "runs").iterdir()) == runs_before | {peer["id"]}
    assert snapshot_tree(home / "runs" / peer["id"]) == peer["tree"]
    assert (home / "current_run").read_bytes() == peer["pointer"]
    assert snapshot_tree(home / "runs" / rid) == parent_before
