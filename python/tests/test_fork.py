"""第 5 轮回归：run.fork 健康路径成功 + 失败零残留（孤儿目录 / current_run 指针）。"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from conftest import call_facade, create_run, make_home, write_article


def _seed_healthy(home: Path) -> tuple[str, str]:
    run_id = create_run(home, "父")
    ver = write_article(home, run_id, "父正文")
    return run_id, ver


def test_fork_healthy_path_success(tmp_path):
    home = make_home(tmp_path)
    run_id, ver = _seed_healthy(home)

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
