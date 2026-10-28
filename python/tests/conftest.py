"""共享 fixtures：临时 WEWRITE_HOME、运行 facade 子进程、构建各种 state 形态。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PYTHON_SRC = REPO_ROOT / "python" / "src"
VENV_PY = os.environ.get("FACADE_PYTHON", sys.executable)


def facade_env(home: str) -> dict[str, str]:
    env = os.environ.copy()
    env["WEWRITE_HOME"] = home
    env["PYTHONPATH"] = str(PYTHON_SRC) + os.pathsep + env.get("PYTHONPATH", "")
    return env


def call_facade(home: str, operation: str, payload: dict, request_id: str = "r") -> dict:
    """起一个 facade 子进程，单请求单响应。"""
    req = {"protocol": 1, "requestId": request_id, "operation": operation, "payload": payload}
    proc = subprocess.run(
        [VENV_PY, "-m", "dsh_wewrite_facade"],
        input=json.dumps(req, ensure_ascii=False) + "\n",
        capture_output=True,
        text=True,
        env=facade_env(home),
        timeout=60,
    )
    assert proc.returncode == 0, f"facade 非零退出: {proc.returncode}\nstderr={proc.stderr}"
    out = proc.stdout.strip()
    assert out, "facade 无输出"
    resp = json.loads(out)
    assert resp.get("protocol") == 1
    assert resp.get("requestId") == request_id
    return resp


def make_home(tmp_path: Path) -> Path:
    home = tmp_path / "WEWRITE_HOME"
    home.mkdir(parents=True)
    return home


def create_run(home: Path, topic: str = "测试主题") -> str:
    resp = call_facade(str(home), "run.create", {
        "requestId": "c1",
        "topic": topic,
        "mode": "draft",
        "visualMode": "none",
        "themeId": "professional-clean",
    })
    assert resp["ok"] is True, resp
    return resp["data"]["runId"]


def write_article(home: Path, run_id: str, content: str) -> str:
    """直接写 article.md 并返回其 SHA-256（绕过 facade 以构造种子文件）。"""
    import hashlib
    run_dir = home / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    data = content.encode("utf-8")
    (run_dir / "article.md").write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def snapshot_tree(home: Path) -> dict[str, tuple[int, int]]:
    """返回 (relpath -> (size, mtime_ns)) 的完整目录清单（不含自身锁文件外的增量）。"""
    result = {}
    for root, dirs, files in os.walk(home):
        for name in files:
            p = Path(root) / name
            rel = str(p.relative_to(home))
            st = p.stat()
            result[rel] = (st.st_size, st.st_mtime_ns)
    return result
