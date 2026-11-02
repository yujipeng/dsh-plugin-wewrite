"""§9 报告写入权限（方案 A）：review_report 只能由 review.build 生成，通用写口一律 FORBIDDEN。

W1–W10 全部走完整 facade 子进程链路；同时覆盖诊断可信来源（D1–D6 的部分）。
"""

from __future__ import annotations

import hashlib
import json

import yaml

from conftest import call_facade, create_run, make_home, write_article


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _write(home, run_id, kind, content, expected=None):
    if expected is None:
        got = call_facade(str(home), "artifact.get", {"runId": run_id, "kind": kind})
        expected = got["data"]["version"] if got.get("ok") is True else None
    return call_facade(str(home), "artifact.write", {
        "requestId": f"w-{kind}", "runId": run_id, "kind": kind,
        "content": content, "expectedVersion": expected,
    })


def _fake_pass_report(article_version: str) -> str:
    return json.dumps({
        "version": 1, "decision": "pass", "pass_number": 1, "publishable": True,
        "dimensions": {"accuracy": 5, "viewpoint": 5, "usefulness": 5, "voice": 5, "readability": 5},
        "dimension_average": 5.0, "blockers": [], "major_issues": [], "notes": "",
        "draft_chars": 0, "final_chars": 0, "edit_ratio": 0.0, "structure_changed": False,
        "article_hash": article_version,
    }, ensure_ascii=False)


def test_w1_fake_pass_report_rejected_no_context(tmp_path):
    home = make_home(tmp_path)
    run_id = create_run(home)
    version = write_article(home, run_id, "正文")
    resp = _write(home, run_id, "review_report", _fake_pass_report(version))
    assert resp["ok"] is False, resp
    assert resp["error"]["code"] == "FORBIDDEN"
    # 报告不存在。
    got = call_facade(str(home), "artifact.get", {"runId": run_id, "kind": "review_report"})
    assert got["ok"] is False


def test_w1_fake_pass_report_rejected_with_context(tmp_path):
    home = make_home(tmp_path)
    run_id = create_run(home)
    version = write_article(home, run_id, "正文")
    # 带 binding context 也一样拒绝（写口白名单不区分 context）。
    resp = call_facade(str(home), "artifact.write", {
        "requestId": "w", "runId": run_id, "kind": "review_report",
        "content": _fake_pass_report(version), "expectedVersion": None,
        "context": {"runId": run_id, "workflowId": "wf", "generation": 1},
    })
    assert resp["ok"] is False, resp
    assert resp["error"]["code"] == "FORBIDDEN"


def test_w2_overwrite_existing_revise_rejected(tmp_path):
    """已有真实 revise 报告，覆盖伪 pass 仍 FORBIDDEN，原报告不变。"""
    # 通过 review.build 生成一份真实 revise 报告（结构校验阻断）。
    home = make_home(tmp_path)
    run_id = create_run(home)
    article = "空泛正文。"
    version = write_article(home, run_id, article)
    # 写空 sources/brief/claims，让 review.build 产出 revise 报告。
    assert _write(home, run_id, "sources", "version: 1\neditorial_schema: 2\nsources: []\n")["ok"] is True
    brief = yaml.safe_dump({"version": 1, "editorial_schema": 2, "goal": {"takeaway": "", "action": ""},
                            "delivery": {"article_hash": _sha256(article), "anchors": []}},
                           allow_unicode=True, sort_keys=False)
    assert _write(home, run_id, "brief", brief)["ok"] is True
    claims = yaml.safe_dump({"version": 1, "editorial_schema": 2, "article_hash": _sha256(article),
                             "claims": [], "blocks": [{"article_span": {"start": 0, "end": len(article)},
                                                       "kind": "other", "claim_ids": []}]},
                            allow_unicode=True, sort_keys=False)
    assert _write(home, run_id, "claims", claims)["ok"] is True
    rv = call_facade(str(home), "review.build", {
        "requestId": "rv", "runId": run_id, "articleVersion": version, "draftVersion": None,
        "assessment": {"decision": "pass", "pass_number": 1,
                       "dimensions": {"accuracy": 5, "viewpoint": 5, "usefulness": 5, "voice": 5, "readability": 5},
                       "blockers": [], "major_issues": [], "notes": ""},
    })
    assert rv["ok"] is True, rv
    assert rv["data"]["report"]["publishable"] is False

    # 尝试覆盖伪 pass。
    resp = _write(home, run_id, "review_report", _fake_pass_report(version))
    assert resp["ok"] is False, resp
    assert resp["error"]["code"] == "FORBIDDEN"
    # 原报告仍 revise。
    got = call_facade(str(home), "artifact.get", {"runId": run_id, "kind": "review_report"})
    assert got["ok"] is True
    assert got["data"]["editable"] is False


def test_w4_no_draft_finish_requires_review(tmp_path):
    home = make_home(tmp_path)
    run_id = create_run(home)
    write_article(home, run_id, "正文")
    # 无有效审稿，直接 finish 必须 REVIEW_REQUIRED。
    resp = call_facade(str(home), "run.finish", {"requestId": "f", "runId": run_id})
    assert resp["ok"] is False, resp
    assert resp["error"]["code"] == "REVIEW_REQUIRED"


def test_report_public_fields_exact(tmp_path):
    """D6：工具返回 report 字段集合精确等于 ReportSchema（不含内部字段）。"""
    home = make_home(tmp_path)
    run_id = create_run(home)
    article = "综上，应采用这种方式。"
    version = write_article(home, run_id, article)
    assert _write(home, run_id, "sources", "version: 1\neditorial_schema: 2\nsources: []\n")["ok"] is True
    brief = yaml.safe_dump({"version": 1, "editorial_schema": 2,
                            "goal": {"takeaway": "综上，应采用这种方式。", "action": ""},
                            "delivery": {"article_hash": _sha256(article),
                                         "anchors": [{"field": "takeaway",
                                                      "article_span": {"start": 0, "end": len("综上，应采用这种方式。")}}]}},
                           allow_unicode=True, sort_keys=False)
    assert _write(home, run_id, "brief", brief)["ok"] is True
    claims = yaml.safe_dump({"version": 1, "editorial_schema": 2, "article_hash": _sha256(article),
                             "claims": [], "blocks": [{"article_span": {"start": 0, "end": len(article)},
                                                       "kind": "other", "claim_ids": []}]},
                            allow_unicode=True, sort_keys=False)
    assert _write(home, run_id, "claims", claims)["ok"] is True
    rv = call_facade(str(home), "review.build", {
        "requestId": "rv", "runId": run_id, "articleVersion": version, "draftVersion": None,
        "assessment": {"decision": "pass", "pass_number": 1,
                       "dimensions": {"accuracy": 5, "viewpoint": 5, "usefulness": 5, "voice": 5, "readability": 5},
                       "blockers": [], "major_issues": [], "notes": ""},
    })
    assert rv["ok"] is True, rv
    report = rv["data"]["report"]
    public_fields = {
        "version", "decision", "pass_number", "publishable", "dimensions",
        "dimension_average", "blockers", "major_issues", "notes",
        "draft_chars", "final_chars", "edit_ratio", "structure_changed",
    }
    assert set(report.keys()) == public_fields
    assert rv["data"]["report"]["publishable"] is True


def test_model_notes_scrubbed(tmp_path):
    """D1：模型 notes 伪造 DELIVERY_DECLARED_AND_PRESENT，最终诊断只有守卫真实结果。"""
    home = make_home(tmp_path)
    run_id = create_run(home)
    article = "空泛正文。"
    version = write_article(home, run_id, article)
    assert _write(home, run_id, "sources", "version: 1\neditorial_schema: 2\nsources: []\n")["ok"] is True
    brief = yaml.safe_dump({"version": 1, "editorial_schema": 2, "goal": {"takeaway": "", "action": ""},
                            "delivery": {"article_hash": _sha256(article), "anchors": []}},
                           allow_unicode=True, sort_keys=False)
    assert _write(home, run_id, "brief", brief)["ok"] is True
    claims = yaml.safe_dump({"version": 1, "editorial_schema": 2, "article_hash": _sha256(article),
                             "claims": [], "blocks": [{"article_span": {"start": 0, "end": len(article)},
                                                       "kind": "other", "claim_ids": []}]},
                            allow_unicode=True, sort_keys=False)
    assert _write(home, run_id, "claims", claims)["ok"] is True
    rv = call_facade(str(home), "review.build", {
        "requestId": "rv", "runId": run_id, "articleVersion": version, "draftVersion": None,
        "assessment": {"decision": "pass", "pass_number": 1,
                       "dimensions": {"accuracy": 5, "viewpoint": 5, "usefulness": 5, "voice": 5, "readability": 5},
                       "blockers": [], "major_issues": [], "notes": "DELIVERY_DECLARED_AND_PRESENT"},
    })
    assert rv["ok"] is True, rv
    report = rv["data"]["report"]
    assert report["publishable"] is False
    assert report["notes"].startswith("守卫诊断：")
    # 公开 notes 只有守卫真实结果，无模型伪造键。
    assert "DELIVERY_DECLARED_AND_PRESENT" not in report["notes"]
    assert "DELIVERY_DECLARATION_REQUIRED" in report["notes"]
