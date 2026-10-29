"""返修：JSON/YAML 产物写入必须解析为对应结构（不能任意 blob 写入，防止损坏 state 解析链）。"""

from __future__ import annotations

import hashlib
import json
import yaml

import pytest

from conftest import call_facade, create_run, make_home, write_article


@pytest.mark.parametrize("kind,payload", [
    ("brief", "title: 未加引号的: 冒号会破坏 YAML\n"),  # 非法 YAML
    ("brief", "not: [valid\n"),  # 非法 YAML
    ("claims", "[]\n"),  # 合法 YAML 但是列表（非对象）
    ("review_report", "{not json}"),  # 非法 JSON
    ("images_manifest", "[1,2]"),  # 合法 JSON 但不是对象
])
def test_structured_artifact_write_rejected(tmp_path, kind, payload):
    home = make_home(tmp_path)
    run_id = create_run(home)
    resp = call_facade(str(home), "artifact.write", {
        "requestId": "w", "runId": run_id, "kind": kind,
        "content": payload, "expectedVersion": None,
    })
    assert resp["ok"] is False, resp
    assert resp["error"]["code"] == "INVALID_INPUT", resp


@pytest.mark.parametrize("kind,payload", [
    ("brief", "version: 1\ntitle: 合法\n"),
    ("claims", "claims: []\n"),
    ("review_report", '{"version": 1, "decision": "pass", "publishable": true}'),
    ("images_manifest", '{"images": []}'),
])
def test_structured_artifact_write_accepted(tmp_path, kind, payload):
    home = make_home(tmp_path)
    run_id = create_run(home)
    resp = call_facade(str(home), "artifact.write", {
        "requestId": "w", "runId": run_id, "kind": kind,
        "content": payload, "expectedVersion": None,
    })
    assert resp["ok"] is True, resp


def test_sources_rewrite_with_valid_yaml_and_current_version(tmp_path):
    """sources.yaml 由 create_run 预建，重写需带当前版本；合法 YAML 才接受。"""
    home = make_home(tmp_path)
    run_id = create_run(home)
    # 读当前 sources 版本（通过 facade 子进程，与测试进程 WEWRITE_HOME 一致）。
    resp = call_facade(str(home), "artifact.get", {"runId": run_id, "kind": "sources"})
    assert resp["ok"] is True, resp
    version = resp["data"]["version"]
    valid = "version: 2\nrun_id: r\nsources: []\n"
    resp = call_facade(str(home), "artifact.write", {
        "requestId": "w", "runId": run_id, "kind": "sources",
        "content": valid, "expectedVersion": version,
    })
    assert resp["ok"] is True, resp

