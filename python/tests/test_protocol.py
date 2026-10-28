"""协议层测试：operation 白名单、非法请求、字节流超限、错误中文无栈无秘密。"""

from __future__ import annotations

import json

from conftest import call_facade, make_home


def test_unknown_operation_rejected(tmp_path):
    home = make_home(tmp_path)
    resp = call_facade(str(home), "os.system", {"cmd": "ls"})
    assert resp["ok"] is False
    assert resp["error"]["code"] == "INVALID_INPUT"


def test_python_module_argv_rejected(tmp_path):
    home = make_home(tmp_path)
    resp = call_facade(str(home), "wewrite.runs", {"run_id": "x"})
    assert resp["ok"] is False


def test_malformed_json_gives_chinese_error(tmp_path):
    import subprocess, sys, json as _json
    env = __import__("conftest", fromlist=["facade_env"]).facade_env(str(make_home(tmp_path)))
    proc = subprocess.run(
        [sys.executable, "-m", "dsh_wewrite_facade"],
        input="not json", capture_output=True, text=True, env=env, timeout=30,
    )
    resp = _json.loads(proc.stdout.strip())
    assert resp["ok"] is False
    assert "合法" in resp["error"]["message"]


def test_oversize_request_rejected(tmp_path):
    home = make_home(tmp_path)
    resp = call_facade(str(home), "article.save", {
        "requestId": "r",
        "runId": "20261028-120000-abcdef",
        "markdown": "x" * (3 * 1024 * 1024),
        "expectedVersion": None,
        "metadata": {"title": "t", "digest": "d", "themeId": "professional-clean"},
    })
    assert resp["ok"] is False
    assert resp["error"]["code"] == "INVALID_INPUT"


def test_errors_no_stack_no_secret(tmp_path):
    home = make_home(tmp_path)
    resp = call_facade(str(home), "artifact.get", {"runId": "20261028-120000-abcdef", "kind": "article"})
    assert resp["ok"] is False
    text = json.dumps(resp, ensure_ascii=False)
    assert "Traceback" not in text
    assert "File " not in text
    assert str(home) not in text
