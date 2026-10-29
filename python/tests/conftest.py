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


# S4 推送预检要求正文 200–20000 字：一个满足下限的种子正文（去空白后 >200 字）。
PUBLISHABLE_BODY = (
    "# 测试标题\n\n"
    + "这是一篇用于微信草稿推送验收的测试正文。" * 12
    + "\n\n"
    + "正文需要满足推送预检的字数下限要求，"
    "因此这里补充足够多的内容以保证去空白字符后的字数超过两百字，"
    "同时不含任何真实微信凭据或外部网络引用。"
)


def publish_binding(home: Path, run_id: str, generation: int = 1, workflow: str = "wf-a") -> dict:
    return call_facade(str(home), "binding.publish", {
        "requestId": f"b{generation}",
        "runId": run_id,
        "workflowId": workflow,
        "generation": generation,
        "agentId": f"agent-{generation}",
        "sessionId": f"session-{generation}",
        "workspaceId": "ws-1",
        "phase": "writing",
    })


def put_cover(home: Path, run_id: str) -> dict:
    """登记一张 1x1 PNG 封面资产，返回 {assetId, contentHash}。"""
    import base64
    import struct
    import zlib

    def _png() -> bytes:
        sig = b"\x89PNG\r\n\x1a\n"

        def chunk(t: bytes, d: bytes) -> bytes:
            return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

        ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
        idat = zlib.compress(b"\x00\xff\x00\x00")
        return sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")

    b64 = base64.b64encode(_png()).decode("ascii")
    resp = call_facade(str(home), "asset.put", {
        "requestId": "cover-1",
        "runId": run_id,
        "role": "cover",
        "mediaType": "image/png",
        "base64": b64,
        "expectedArticleVersion": None,
    })
    assert resp["ok"] is True, resp
    return resp["data"]


def write_review(home: Path, run_id: str, article_version: str, publishable: bool = True) -> None:
    """直接写 review-report.json（绑定 article_version 哈希），供 build_gate 判定。"""
    import json
    report = {
        "version": 1,
        "decision": "pass" if publishable else "revise",
        "pass_number": 1,
        "publishable": publishable,
        "dimensions": {"accuracy": 5, "viewpoint": 5, "usefulness": 5, "voice": 5, "readability": 5},
        "dimension_average": 5.0,
        "blockers": [],
        "major_issues": [],
        "notes": "",
        "draft_chars": 3,
        "final_chars": 3,
        "edit_ratio": 0.0,
        "structure_changed": False,
        "article_hash": article_version,
    }
    (home / "runs" / run_id / "review-report.json").write_text(
        json.dumps(report, ensure_ascii=False), encoding="utf-8"
    )


def make_publishable_run(home: Path, run_id: str, body: str = PUBLISHABLE_BODY, workflow: str = "wf-a") -> tuple[str, dict]:
    """构造一个可通过 S4 预检的 run：200+ 字正文 + 封面资产 + 通过审稿 + binding。

    返回 (article_version, cover)。prepare/send 测试共用，避免各测试散落「短正文/假封面」。
    """
    version = write_article(home, run_id, body)
    assert publish_binding(home, run_id, 1, workflow=workflow)["ok"] is True
    cover = put_cover(home, run_id)
    write_review(home, run_id, version, True)
    return version, cover


def snapshot_tree(home: Path) -> dict[str, tuple[str, int, int]]:
    """返回 (relpath -> (sha256, size, mtime_ns)) 的完整目录清单，字节用哈希比对。"""
    import hashlib
    result = {}
    for root, dirs, files in os.walk(home):
        for name in files:
            p = Path(root) / name
            rel = str(p.relative_to(home))
            st = p.stat()
            digest = hashlib.sha256(p.read_bytes()).hexdigest()
            result[rel] = (digest, st.st_size, st.st_mtime_ns)
    return result
