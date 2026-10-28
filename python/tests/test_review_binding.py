"""审稿绑定必须可验证；缺失、非法、过期均不得通过。"""

import json

import pytest

from conftest import call_facade, create_run, make_home, write_article


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
def test_bound_review_requires_literal_true(tmp_path, binding_key, publishable):
    home = make_home(tmp_path)
    run_id = create_run(home)
    version = write_article(home, run_id, "正文")
    (home / "runs" / run_id / "review-report.json").write_text(
        json.dumps({"publishable": publishable, binding_key: version}), encoding="utf-8")
    response = call_facade(str(home), "run.detail", {"runId": run_id})
    assert response["ok"] is True
    gate = response["data"]["gate"]
    assert gate["publishable"] is (publishable is True)
    assert gate["articleVersion"] == version
    assert gate["issues"][0]["code"] == ("REVIEW_PASSED" if publishable is True else "REVIEW_REQUIRED")
