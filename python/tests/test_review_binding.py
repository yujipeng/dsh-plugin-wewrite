"""审稿绑定必须可验证；缺失、非法、旧格式均不得通过（§6/§9 新门禁）。"""

import json

import pytest

from conftest import call_facade, create_run, make_home, write_article


def _valid_report(publishable: bool = True, article_version: str = "a" * 64) -> dict:
    """构造新格式报告夹具（供旧字段对比用）。"""
    return {
        "version": 1, "decision": "pass" if publishable else "revise", "pass_number": 1,
        "publishable": publishable,
        "dimensions": {"accuracy": 5, "viewpoint": 5, "usefulness": 5, "voice": 5, "readability": 5},
        "dimension_average": 5.0, "blockers": [], "major_issues": [], "notes": "",
        "draft_chars": 0, "final_chars": 0, "edit_ratio": 0.0, "structure_changed": False,
    }


@pytest.mark.parametrize("binding", [{}, {"article_hash": None}, {"article_hash": ""},
                                    {"article_hash": "invalid"}, {"article_hash": True}])
def test_unbound_review_cannot_pass(tmp_path, binding):
    home = make_home(tmp_path)
    run_id = create_run(home)
    version = write_article(home, run_id, "正文")
    (home / "runs" / run_id / "review-report.json").write_text(
        json.dumps({"publishable": True, **binding}), encoding="utf-8")
    response = call_facade(str(home), "run.detail", {"runId": run_id})
    assert response["ok"] is True
    gate = response["data"]["gate"]
    assert gate["publishable"] is False
    assert gate["articleVersion"] == version
    assert gate["issues"][0]["code"] == "REVIEW_BINDING_MISSING"
    assert gate["issues"][0]["blocking"] is True
    assert all(i["code"] != "REVIEW_PASSED" for i in gate["issues"])


@pytest.mark.parametrize("binding", [{}, {"article_hash": "a" * 64}])
def test_review_without_article_is_rejected(tmp_path, binding):
    home = make_home(tmp_path)
    run_id = create_run(home)
    (home / "runs" / run_id / "review-report.json").write_text(
        json.dumps({"publishable": True, **binding}), encoding="utf-8")
    response = call_facade(str(home), "run.detail", {"runId": run_id})
    assert response["ok"] is False
    assert response["error"]["code"] == "ARTIFACT_MISSING"


@pytest.mark.parametrize("binding_key", ["article_hash", "articleVersion"])
@pytest.mark.parametrize("publishable", [True, "true", 1, False])
def test_old_format_report_requires_review(tmp_path, binding_key, publishable):
    """旧格式报告（无 editorial_verdict/input_hashes）→ 不可发布，REVIEW_BINDING_MISSING。"""
    home = make_home(tmp_path)
    run_id = create_run(home)
    version = write_article(home, run_id, "正文")
    (home / "runs" / run_id / "review-report.json").write_text(
        json.dumps({"publishable": publishable, binding_key: version}), encoding="utf-8")
    response = call_facade(str(home), "run.detail", {"runId": run_id})
    assert response["ok"] is True
    gate = response["data"]["gate"]
    assert gate["publishable"] is False
    assert gate["articleVersion"] == version
    assert gate["issues"][0]["code"] == "REVIEW_BINDING_MISSING"
