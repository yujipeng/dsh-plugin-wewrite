"""返修 5：统一严格输入校验层（§3.1/§3.3）的 6 条完整链路负例。

每条只改一处、其余保持合法 schema2 单事实样本；期望 decision=revise、
publishable=false，blockers 含 [EVIDENCE_REFERENCE_INVALID] 或 [EVIDENCE_INPUT_REQUIRED]。
全部走完整 review.build（真实 facade 子进程）。
"""

from __future__ import annotations

import hashlib
import json
import re

import yaml

from conftest import call_facade, create_run, make_home, write_article


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _write(home, run_id, kind, content):
    got = call_facade(str(home), "artifact.get", {"runId": run_id, "kind": kind})
    expected = got["data"]["version"] if got.get("ok") is True else None
    return call_facade(str(home), "artifact.write", {
        "requestId": f"w-{kind}", "runId": run_id, "kind": kind,
        "content": content, "expectedVersion": expected,
    })


FACT = "90% 的用户支持这种方式。"
CONCLUSION = "综上，应采用这种方式。"


def _evidence(text: str) -> dict:
    return {"text": text, "sha256": _sha256(text), "locator": "第一节"}


def _base_sources() -> dict:
    return {"version": 1, "editorial_schema": 2, "run_id": "r",
            "sources": [{"id": "s1", "status": "verified", "url": "https://e/report",
                         "claim": FACT, "evidence": _evidence(FACT)}]}


def _base_claims(article: str) -> dict:
    idx = article.index(FACT)
    return {"version": 1, "editorial_schema": 2, "article_hash": _sha256(article),
            "claims": [{"id": "C1", "type": "fact", "text": FACT,
                        "article_span": {"start": idx, "end": idx + len(FACT)},
                        "source_ids": ["s1"],
                        "evidence_refs": [{"source_id": "s1", "evidence_sha256": _sha256(FACT),
                                          "mode": "verbatim", "quote": FACT}]}],
            "blocks": [{"article_span": {"start": 0, "end": len(article)}, "kind": "fact",
                        "claim_ids": ["C1"]}]}


def _base_brief(article: str) -> dict:
    cidx = article.index(CONCLUSION)
    return {"version": 1, "editorial_schema": 2,
            "goal": {"takeaway": CONCLUSION, "action": ""},
            "delivery": {"article_hash": _sha256(article),
                         "anchors": [{"field": "takeaway",
                                      "article_span": {"start": cidx, "end": cidx + len(CONCLUSION)}}]}}


def _run(home, sources: dict, claims: dict, brief: dict, article: str):
    run_id = create_run(home)
    version = write_article(home, run_id, article)
    assert _write(home, run_id, "sources", yaml.safe_dump(sources, allow_unicode=True, sort_keys=False))["ok"] is True
    assert _write(home, run_id, "brief", yaml.safe_dump(brief, allow_unicode=True, sort_keys=False))["ok"] is True
    assert _write(home, run_id, "claims", yaml.safe_dump(claims, allow_unicode=True, sort_keys=False))["ok"] is True
    return call_facade(str(home), "review.build", {
        "requestId": "rv", "runId": run_id, "articleVersion": version, "draftVersion": None,
        "assessment": {"decision": "pass", "pass_number": 1,
                       "dimensions": {"accuracy": 5, "viewpoint": 5, "usefulness": 5, "voice": 5, "readability": 5},
                       "blockers": [], "major_issues": [], "notes": ""},
    })


def _codes(blockers: list[str]) -> set[str]:
    return {m.group(1) for m in re.finditer(r"\[([A-Z_]+)\]", " ".join(blockers))}


def _assert_blocked(resp, expect_codes):
    assert resp["ok"] is True, resp
    report = resp["data"]["report"]
    assert report["decision"] == "revise", report
    assert report["publishable"] is False
    codes = _codes(report["blockers"])
    assert any(c in codes for c in expect_codes), report["blockers"]


def test_input1_dangling_evidence_ref(tmp_path):
    """#1 source_ids=[s1] 有效，evidence_refs 多一条 ghost → EVIDENCE_REFERENCE_INVALID。"""
    article = f"{CONCLUSION} {FACT}"
    sources = _base_sources()
    claims = _base_claims(article)
    claims["claims"][0]["evidence_refs"].append(
        {"source_id": "ghost", "evidence_sha256": _sha256(FACT), "mode": "verbatim", "quote": FACT})
    brief = _base_brief(article)
    resp = _run(make_home(tmp_path), sources, claims, brief, article)
    _assert_blocked(resp, ("EVIDENCE_REFERENCE_INVALID", "EVIDENCE_INPUT_REQUIRED"))


def test_input2_extra_verified_ref_not_checked(tmp_path):
    """#2 evidence_refs 多一条指向真实 verified s2 → EVIDENCE_REFERENCE_INVALID（双向闭合）。"""
    article = f"{CONCLUSION} {FACT}"
    sources = _base_sources()
    sources["sources"].append({"id": "s2", "status": "verified", "url": "https://e/2",
                               "claim": FACT, "evidence": _evidence(FACT)})
    claims = _base_claims(article)
    claims["claims"][0]["evidence_refs"].append(
        {"source_id": "s2", "evidence_sha256": _sha256(FACT), "mode": "verbatim", "quote": FACT})
    brief = _base_brief(article)
    resp = _run(make_home(tmp_path), sources, claims, brief, article)
    _assert_blocked(resp, ("EVIDENCE_REFERENCE_INVALID", "EVIDENCE_INPUT_REQUIRED"))


def test_input3_claims_not_array(tmp_path):
    """#3 claims.claims 为字符串 → EVIDENCE_INPUT_REQUIRED。"""
    article = f"{CONCLUSION} {FACT}"
    sources = _base_sources()
    claims = _base_claims(article)
    claims["claims"] = "不是数组"
    claims["blocks"] = [{"article_span": {"start": 0, "end": len(article)}, "kind": "other", "claim_ids": []}]
    brief = _base_brief(article)
    resp = _run(make_home(tmp_path), sources, claims, brief, article)
    _assert_blocked(resp, ("EVIDENCE_REFERENCE_INVALID", "EVIDENCE_INPUT_REQUIRED"))


def test_input4_duplicate_source_id_bad_first(tmp_path):
    """#4a sources 两条同 id=s1 且都 valid，坏条目在前（指向另一证据）→ EVIDENCE_INPUT_REQUIRED。"""
    article = f"{CONCLUSION} {FACT}"
    sources = _base_sources()
    other = {"id": "s1", "status": "verified", "url": "https://e/other", "claim": "另一段事实",
             "evidence": _evidence("另一段事实")}
    good = sources["sources"][0]
    sources["sources"] = [other, good]
    claims = _base_claims(article)
    brief = _base_brief(article)
    resp = _run(make_home(tmp_path), sources, claims, brief, article)
    _assert_blocked(resp, ("EVIDENCE_REFERENCE_INVALID", "EVIDENCE_INPUT_REQUIRED"))


def test_input4_duplicate_source_id_good_first(tmp_path):
    """#4b 好条目在前、另一条同 id 在后（均 valid）→ 同样 EVIDENCE_INPUT_REQUIRED。"""
    article = f"{CONCLUSION} {FACT}"
    sources = _base_sources()
    good = sources["sources"][0]
    other = {"id": "s1", "status": "verified", "url": "https://e/other", "claim": "另一段事实",
             "evidence": _evidence("另一段事实")}
    sources["sources"] = [good, other]
    claims = _base_claims(article)
    brief = _base_brief(article)
    resp = _run(make_home(tmp_path), sources, claims, brief, article)
    _assert_blocked(resp, ("EVIDENCE_REFERENCE_INVALID", "EVIDENCE_INPUT_REQUIRED"))


def test_input5_duplicate_claim_id(tmp_path):
    """#5 claims.claims 两条同 id=C1 → EVIDENCE_INPUT_REQUIRED。"""
    article = f"{CONCLUSION} {FACT}"
    sources = _base_sources()
    claims = _base_claims(article)
    claims["claims"] = [claims["claims"][0], dict(claims["claims"][0])]
    brief = _base_brief(article)
    resp = _run(make_home(tmp_path), sources, claims, brief, article)
    _assert_blocked(resp, ("EVIDENCE_REFERENCE_INVALID", "EVIDENCE_INPUT_REQUIRED"))


def test_input6_unknown_enum_type(tmp_path):
    """#6 fact 主张 type 写成大写 Fact，blocks 相应 other → EVIDENCE_INPUT_REQUIRED。"""
    article = f"{CONCLUSION} {FACT}"
    sources = _base_sources()
    claims = _base_claims(article)
    claims["claims"][0]["type"] = "Fact"
    claims["blocks"] = [{"article_span": {"start": 0, "end": len(article)}, "kind": "other", "claim_ids": []}]
    brief = _base_brief(article)
    resp = _run(make_home(tmp_path), sources, claims, brief, article)
    _assert_blocked(resp, ("EVIDENCE_REFERENCE_INVALID", "EVIDENCE_INPUT_REQUIRED"))


def test_input6_unknown_enum_kind(tmp_path):
    """#6 补充：block kind 未知值 → EVIDENCE_INPUT_REQUIRED。"""
    article = f"{CONCLUSION} {FACT}"
    sources = _base_sources()
    claims = _base_claims(article)
    claims["blocks"] = [{"article_span": {"start": 0, "end": len(article)}, "kind": "heading", "claim_ids": []}]
    brief = _base_brief(article)
    resp = _run(make_home(tmp_path), sources, claims, brief, article)
    _assert_blocked(resp, ("EVIDENCE_REFERENCE_INVALID", "EVIDENCE_INPUT_REQUIRED"))
