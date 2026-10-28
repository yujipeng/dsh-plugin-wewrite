"""验收 3：create_run 副作用清单与实测一致，逐项断言。"""

from __future__ import annotations

from pathlib import Path

from conftest import call_facade, make_home


def test_create_run_side_effects(tmp_path):
    home = make_home(tmp_path)
    before = set(p.name for p in home.iterdir())

    resp = call_facade(str(home), "run.create", {
        "requestId": "c1",
        "topic": "主题",
        "mode": "draft",
        "visualMode": "none",
        "themeId": "professional-clean",
    })
    assert resp["ok"] is True
    run_id = resp["data"]["runId"]

    # 七个标准目录。
    for sub in ("output", "runs", "exemplars", "corpus", "lessons", "themes", "personas"):
        assert (home / sub).is_dir(), f"缺少标准目录 {sub}"

    # run 目录。
    run_dir = home / "runs" / run_id
    assert run_dir.is_dir()

    # 三个写文件。
    assert (run_dir / "state.yaml").is_file()
    assert (run_dir / "sources.yaml").is_file()
    assert (home / "current_run").is_file()

    # state.yaml 内容合法性。
    import yaml
    state = yaml.safe_load((run_dir / "state.yaml").read_text(encoding="utf-8"))
    assert state["run_id"] == run_id
    assert state["version"] == 4

    # 锁文件由首次显式写操作预建（home 全局锁）。
    assert (home / ".dsh-workbench.lock").is_file()


def test_create_run_idempotent_standard_dirs(tmp_path):
    home = make_home(tmp_path)
    # 已存在标准目录时 create_run 幂等，不报错。
    for sub in ("output", "runs", "exemplars", "corpus", "lessons", "themes", "personas"):
        (home / sub).mkdir(parents=True, exist_ok=True)
    resp = call_facade(str(home), "run.create", {
        "requestId": "c1", "topic": "", "mode": "draft",
        "visualMode": "none", "themeId": "professional-clean",
    })
    assert resp["ok"] is True
