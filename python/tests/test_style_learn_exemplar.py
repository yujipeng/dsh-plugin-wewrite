"""方案 B 批一：style.get/put/validate + learn.record/list + exemplar.put/list/get 正向与负向。

覆盖验收标准：
- 全部经 facade 子进程协议可达（各有正向用例）。
- style.put 原子写 + 写入前备份；注入异常后 style.yaml 保持旧内容（不能半写）。
- schema 校验拒绝 topics 非数组 / target_audience 类型错误 / 未知字段 / blacklist 含非字符串项，
  错误中文且带字段名。
- 路径安全：exemplar id 不接受任意路径（../ / 绝对路径 / 非安全字符）。
- 权限：携带 context（Agent 写路径）返回 FORBIDDEN；wechatApiCalls 恒为 0。
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import yaml

from conftest import call_facade, facade_env, make_home

VALID_STYLE = {
    "name": "零号AI",
    "industry": "科技/互联网",
    "topics": ["AI", "产品设计", "效率工具"],
    "tone": "专业但不学术",
    "theme": "professional-clean",
    "target_audience": "25-40岁互联网从业者",
    "content_style": "干货",
    "blacklist": {"words": ["震惊", "必看"], "topics": ["政治敏感"]},
}


def _style_get(home):
    return call_facade(str(home), "style.get", {})


def _style_put(home, style, **extra):
    payload = {"style": style, **extra}
    return call_facade(str(home), "style.put", payload)


def _ctx():
    return {"runId": "20261103-000000-abcdef", "workflowId": "wf-a", "generation": 1}


# ── style ────────────────────────────────────────────────────────────────────

def test_style_get_not_found(tmp_path):
    home = make_home(tmp_path)
    resp = _style_get(home)
    assert resp["ok"] is False
    assert resp["error"]["code"] == "NOT_FOUND"


def test_style_put_then_get_roundtrip(tmp_path):
    home = make_home(tmp_path)
    put = _style_put(home, VALID_STYLE)
    assert put["ok"] is True, put
    assert put["data"]["exists"] is True
    # 落盘为真实 style.yaml。
    on_disk = home / "style.yaml"
    assert on_disk.exists()

    got = _style_get(home)
    assert got["ok"] is True, got
    data = got["data"]
    assert data["exists"] is True
    assert data["style"]["topics"] == ["AI", "产品设计", "效率工具"]
    assert data["style"]["target_audience"] == "25-40岁互联网从业者"
    assert data["style"]["blacklist"] == {"words": ["震惊", "必看"], "topics": ["政治敏感"]}
    assert data["style"]["content_style"] == "干货"
    # 原文字段：topic-selection.md 依赖的四个字段确实能被读到。
    assert "topics" in data["raw"]
    assert "target_audience" in data["raw"]
    assert "blacklist" in data["raw"]
    assert "content_style" in data["raw"]


def test_style_validate_returns_problem_list(tmp_path):
    home = make_home(tmp_path)
    resp = call_facade(str(home), "style.validate", {"style": VALID_STYLE})
    assert resp["ok"] is True, resp
    assert resp["data"]["valid"] is True
    assert resp["data"]["issues"] == []


def test_style_put_rejects_topics_not_array(tmp_path):
    home = make_home(tmp_path)
    bad = {**VALID_STYLE, "topics": "AI"}
    resp = _style_put(home, bad)
    assert resp["ok"] is False
    assert resp["error"]["code"] == "INVALID_INPUT"
    assert "topics" in resp["error"]["message"]


def test_style_put_rejects_target_audience_wrong_type(tmp_path):
    home = make_home(tmp_path)
    bad = {**VALID_STYLE, "target_audience": 123}
    resp = _style_put(home, bad)
    assert resp["ok"] is False
    assert "target_audience" in resp["error"]["message"]


def test_style_put_rejects_unknown_field(tmp_path):
    home = make_home(tmp_path)
    bad = {**VALID_STYLE, "surprise_field": "x"}
    resp = _style_put(home, bad)
    assert resp["ok"] is False
    assert "surprise_field" in resp["error"]["message"]


def test_style_put_rejects_blacklist_non_string_item(tmp_path):
    home = make_home(tmp_path)
    bad = {**VALID_STYLE, "blacklist": {"words": ["ok", 123]}}
    resp = _style_put(home, bad)
    assert resp["ok"] is False
    assert "blacklist" in resp["error"]["message"]


def test_style_put_forbidden_with_agent_context(tmp_path):
    home = make_home(tmp_path)
    resp = _style_put(home, VALID_STYLE, context=_ctx())
    assert resp["ok"] is False
    assert resp["error"]["code"] == "FORBIDDEN"
    assert not (home / "style.yaml").exists()


def test_style_put_backs_up_previous_version(tmp_path):
    home = make_home(tmp_path)
    assert _style_put(home, VALID_STYLE)["ok"] is True
    v1 = (home / "style.yaml").read_bytes()
    second = {**VALID_STYLE, "tone": "轻松有趣"}
    assert _style_put(home, second)["ok"] is True
    v2 = (home / "style.yaml").read_bytes()
    assert v1 != v2
    bak = home / "style.yaml.bak"
    assert bak.exists()
    assert bak.read_bytes() == v1


def test_style_put_atomic_no_partial_write_on_injected_failure(tmp_path):
    """注入 os.replace 失败（临时文件已生成、进入替换步骤才抛）后，style.yaml 保持旧内容不变。

    用独立子进程 replace_fail_facade 走标准协议入口：REPLACE_FAIL_FILE=style.yaml，
    storage._write_file_atomically 里 os.replace 抛 OSError → 临时文件被 unlink →
    协议层收敛为业务失败 INTERNAL。断言的错误码收窄，不再用 in (...) 兜底。
    """
    import json
    import subprocess
    import sys
    from pathlib import Path

    home = make_home(tmp_path)
    assert _style_put(home, VALID_STYLE)["ok"] is True
    old_bytes = (home / "style.yaml").read_bytes()
    old_hash = hashlib.sha256(old_bytes).hexdigest()

    env = facade_env(str(home))
    env["REPLACE_FAIL_FILE"] = "style.yaml"
    req = json.dumps({
        "protocol": 1, "requestId": "r", "operation": "style.put",
        "payload": {"style": {**VALID_STYLE, "tone": "轻松有趣"}},
    }, ensure_ascii=False)
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve().parent / "replace_fail_facade.py")],
        input=req + "\n", capture_output=True, text=True, env=env, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    resp = json.loads(proc.stdout)

    assert resp["ok"] is False, resp
    # 注入的 OSError 在协议层收敛为业务失败 INTERNAL（不泄露栈），错误码收窄不兜底。
    assert resp["error"]["code"] == "INTERNAL", resp
    # style.yaml 字节不变，未被半写。
    after = (home / "style.yaml").read_bytes()
    assert hashlib.sha256(after).hexdigest() == old_hash
    # 没有遗留 .tmp 半成品文件。
    leftovers = [p for p in home.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == []


def test_wechat_api_calls_stay_zero(tmp_path):
    home = make_home(tmp_path)
    _style_put(home, VALID_STYLE)
    _style_get(home)
    call_facade(str(home), "style.validate", {"style": VALID_STYLE})
    call_facade(str(home), "learn.record", {
        "draft": "# 初稿\n", "final": "# 终稿\n",
        "diffSummary": {"lines_added": 0, "lines_deleted": 0},
        "patterns": [],
    })
    call_facade(str(home), "learn.list", {})
    call_facade(str(home), "exemplar.put", {"title": "示例", "content": "# 正文\n"})
    call_facade(str(home), "exemplar.list", {})
    diag = call_facade(str(home), "diagnose", {})
    assert diag["ok"] is True
    assert diag["data"]["counters"]["wechatApiCalls"] == 0


# ── learn ────────────────────────────────────────────────────────────────────

def test_learn_record_then_list(tmp_path):
    home = make_home(tmp_path)
    rec = call_facade(str(home), "learn.record", {
        "draft": "# 初稿\n正文一\n",
        "final": "# 终稿\n正文二\n",
        "diffSummary": {"lines_added": 1, "lines_deleted": 1, "char_diff": 0},
        "patterns": [{"type": "expression", "key": "shorter_paragraphs",
                      "description": "长段拆短", "rule": "每段只处理一个操作",
                      "scope": "content_type", "scope_value": "tutorial", "confirmed": False}],
    })
    assert rec["ok"] is True, rec
    assert rec["data"]["file"].startswith("lessons/")
    lesson_path = home / rec["data"]["file"]
    assert lesson_path.exists()

    lst = call_facade(str(home), "learn.list", {})
    assert lst["ok"] is True, lst
    assert lst["data"]["total"] == 1
    assert lst["data"]["items"][0]["patternCount"] == 1


def test_learn_record_forbidden_with_agent_context(tmp_path):
    home = make_home(tmp_path)
    resp = call_facade(str(home), "learn.record", {
        "draft": "# 初稿\n", "final": "# 终稿\n",
        "diffSummary": {}, "patterns": [], "context": _ctx(),
    })
    assert resp["ok"] is False
    assert resp["error"]["code"] == "FORBIDDEN"


# ── exemplar ────────────────────────────────────────────────────────────────

def test_exemplar_put_list_get_roundtrip(tmp_path):
    home = make_home(tmp_path)
    put = call_facade(str(home), "exemplar.put", {
        "title": "一篇范文", "content": "# 范文\n\n这是范文正文。\n",
        "source": "参考账号", "category": "tech-opinion",
        "ownership": "third_party", "authenticity": "published",
    })
    assert put["ok"] is True, put
    exemplar_id = put["data"]["id"]
    assert len(exemplar_id) == 16

    lst = call_facade(str(home), "exemplar.list", {})
    assert lst["ok"] is True, lst
    assert lst["data"]["total"] == 1
    assert lst["data"]["items"][0]["id"] == exemplar_id

    got = call_facade(str(home), "exemplar.get", {"id": exemplar_id})
    assert got["ok"] is True, got
    assert got["data"]["title"] == "一篇范文"
    assert got["data"]["personal_materials_reusable"] is False
    assert "范文正文" in got["data"]["content"]


def test_exemplar_get_rejects_path_like_ids(tmp_path):
    home = make_home(tmp_path)
    for evil in ("../etc/passwd", "/etc/passwd", "..%2f..%2fetc", "a" * 17, "ABC-DEF-123"):
        resp = call_facade(str(home), "exemplar.get", {"id": evil})
        assert resp["ok"] is False, f"{evil} 应被拒绝"
        assert resp["error"]["code"] in ("INVALID_INPUT", "NOT_FOUND")
    # 确认没有越界文件被读取/创建。
    assert not (home / "etc").exists()


def test_exemplar_put_forbidden_with_agent_context(tmp_path):
    home = make_home(tmp_path)
    resp = call_facade(str(home), "exemplar.put", {
        "title": "范文", "content": "# 正文\n", "context": _ctx(),
    })
    assert resp["ok"] is False
    assert resp["error"]["code"] == "FORBIDDEN"


def test_exemplar_get_not_found(tmp_path):
    home = make_home(tmp_path)
    resp = call_facade(str(home), "exemplar.get", {"id": "0123456789abcdef"})
    assert resp["ok"] is False
    assert resp["error"]["code"] == "NOT_FOUND"


# ── 写入侧 symlink 逃逸负例 ────────────────────────────────────────────────

def test_style_put_rejects_style_symlink_escape(tmp_path):
    """style.yaml 被预置为指向 home 外的 symlink：写入必须拒绝，home 外文件字节不变。"""
    home = make_home(tmp_path)
    outside = tmp_path / "outside.yaml"
    outside.write_text("attacker-seed", encoding="utf-8")
    os.symlink(outside, home / "style.yaml")

    resp = _style_put(home, VALID_STYLE)
    assert resp["ok"] is False, resp
    assert resp["error"]["code"] == "FORBIDDEN", resp
    assert "不是普通文件" in resp["error"]["message"]
    # home 外文件未被改写；style.yaml 仍是指向 outside 的 symlink。
    assert outside.read_text(encoding="utf-8") == "attacker-seed"
    assert (home / "style.yaml").is_symlink()


def test_learn_record_rejects_lessons_symlink_escape(tmp_path):
    """lessons 子目录被预置为指向 home 外的 symlink：写入必须拒绝，home 外文件字节不变。"""
    home = make_home(tmp_path)
    outside = tmp_path / "outside-lessons"
    outside.mkdir()
    seed = outside / "seed.txt"
    seed.write_text("seed", encoding="utf-8")
    os.symlink(outside, home / "lessons")

    resp = call_facade(str(home), "learn.record", {
        "draft": "# 初稿\n", "final": "# 终稿\n",
        "diffSummary": {"lines_added": 0, "lines_deleted": 0}, "patterns": [],
    })
    assert resp["ok"] is False, resp
    assert resp["error"]["code"] in ("FORBIDDEN", "INTERNAL"), resp
    # home 外目录内容不变（未新增 lesson 文件）。
    assert sorted(p.name for p in outside.iterdir()) == ["seed.txt"]
    assert seed.read_text(encoding="utf-8") == "seed"


def test_exemplar_put_rejects_exemplars_symlink_escape(tmp_path):
    """exemplars 子目录被预置为指向 home 外的 symlink：写入必须拒绝，home 外文件字节不变。"""
    home = make_home(tmp_path)
    outside = tmp_path / "outside-exemplars"
    outside.mkdir()
    seed = outside / "seed.txt"
    seed.write_text("seed", encoding="utf-8")
    os.symlink(outside, home / "exemplars")

    resp = call_facade(str(home), "exemplar.put", {"title": "范文", "content": "# 正文\n"})
    assert resp["ok"] is False, resp
    assert resp["error"]["code"] in ("FORBIDDEN", "INTERNAL"), resp
    # home 外目录内容不变（未新增 exemplar 文件）。
    assert sorted(p.name for p in outside.iterdir()) == ["seed.txt"]
    assert seed.read_text(encoding="utf-8") == "seed"
