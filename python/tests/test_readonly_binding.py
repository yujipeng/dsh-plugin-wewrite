"""四轮-1：读面彻底只读——binding.get / draft.prepare 后 binding.json/intents.json 字节与 mtime_ns 不变。"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys

import pytest


def _tree(home):
    """返回 (relpath -> (sha256, size, mtime_ns))。"""
    result = {}
    for root, dirs, files in os.walk(home):
        for name in files:
            p = os.path.join(root, name)
            rel = os.path.relpath(p, home)
            data = open(p, "rb").read()
            st = os.stat(p)
            result[rel] = (hashlib.sha256(data).hexdigest(), st.st_size, st.st_mtime_ns)
    return result


def _call(home, op, payload):
    env = os.environ.copy()
    env["PYTHONPATH"] = os.path.abspath("python/src")
    env["WEWRITE_HOME"] = home
    req = {"protocol": 1, "requestId": "r", "operation": op, "payload": payload}
    proc = subprocess.run([sys.executable, "-m", "dsh_wewrite_facade"],
                          input=json.dumps(req) + "\n", capture_output=True, text=True,
                          env=env, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_read_face_no_write_binding_get_and_draft_prepare(tmp_path):
    home = tmp_path / "WEWRITE_HOME"
    home.mkdir(parents=True)
    env = os.environ.copy()
    env["PYTHONPATH"] = os.path.abspath("python/src")
    # create_run（写面，预建目录/锁设施）
    run_id = _call(str(home), "run.create", {
        "requestId": "c", "topic": "t", "mode": "draft", "visualMode": "none", "themeId": "professional-clean",
    })["data"]["runId"]
    # 写正文
    article = "# 标题\n\n正文".encode("utf-8")
    (home / "runs" / run_id / "article.md").write_bytes(article)

    # 发布 binding + 登记一个 intent（写面）
    _call(str(home), "binding.publish", {
        "requestId": "b", "runId": run_id, "workflowId": "wf-1", "generation": 1,
        "agentId": "a", "sessionId": "s", "workspaceId": "ws-1", "phase": "writing",
    })
    _call(str(home), "draft.prepare", {
        "requestId": "p", "runId": run_id, "expectedVersion": None,
        "coverAssetId": "c", "themeId": "professional-clean",
    })

    before = _tree(str(home))

    # 连续读面（纯读）：binding.get、snapshot、run.detail。这些绝不能写盘。
    _call(str(home), "binding.get", {"runId": run_id})
    _call(str(home), "snapshot", {})
    _call(str(home), "run.detail", {"runId": run_id})

    after = _tree(str(home))
    # 全部既有文件字节 + size + mtime_ns 不变。
    for rel, (sha, size, mtime) in before.items():
        assert rel in after, f"文件消失：{rel}"
        assert after[rel] == (sha, size, mtime), f"文件被写：{rel}"
