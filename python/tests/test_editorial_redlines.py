"""方案 A 定稿设计 v2 验收矩阵（S/T/P/D/W/E）：全部走完整 review.build 链路。

每条反例必须核诊断键（不能只断言 blockers 非空 / publishable=false）。
构造负向来源用例须补齐合法结论声明与正文定位；构造结论用例须补齐合法无事实 claims/blocks。
"""

from __future__ import annotations

import hashlib
import json
import re

import yaml

from conftest import call_facade, create_run, make_home, write_article


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _write_artifact(home, run_id, kind, content, expected=None):
    if expected is None:
        got = call_facade(str(home), "artifact.get", {"runId": run_id, "kind": kind})
        expected = got["data"]["version"] if got.get("ok") is True else None
    return call_facade(str(home), "artifact.write", {
        "requestId": f"w-{kind}", "runId": run_id, "kind": kind,
        "content": content, "expectedVersion": expected,
    })


def _span(text: str, needle: str) -> dict[str, int]:
    idx = text.index(needle)
    return {"start": idx, "end": idx + len(needle)}


def _make_sources(entries: list[dict]) -> str:
    out = []
    for e in entries:
        item = dict(e)
        if "evidence" not in item:
            text = item.get("claim", "")
            item["evidence"] = {"text": text, "sha256": _sha256(text), "locator": "第一节第一段"}
        out.append(item)
    return yaml.safe_dump(
        {"version": 1, "editorial_schema": 2, "run_id": "r", "sources": out},
        allow_unicode=True, sort_keys=False)


def _make_brief(article: str, takeaway: str = "", action: str = "", schema: int | None = 2) -> str:
    data: dict = {
        "version": 1,
        "audience": {"who": "读者", "context": "场景", "question": "问题"},
        "thesis": {"statement": "判断", "novelty": "新", "boundary": "边界", "counterpoint": "反方"},
        "personal_materials": {"available": False, "items": []},
        "framework": "观点",
        "sections": [],
        "constraints": {"desired_length": "1200-2500 字", "must_include": [], "must_avoid": []},
    }
    if schema is not None:
        data["editorial_schema"] = schema
    data["goal"] = {"takeaway": takeaway, "action": action}
    anchors = []
    if takeaway:
        anchors.append({"field": "takeaway", "article_span": _span(article, takeaway)})
    if action:
        anchors.append({"field": "action", "article_span": _span(article, action)})
    data["delivery"] = {"article_hash": _sha256(article), "anchors": anchors}
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False)


def _make_claims(article: str, fact_text: str | None, source: dict | None, quote: str | None = None,
                 schema: int | None = 2, blocks: list[dict] | None = None) -> str:
    data: dict = {"version": 1}
    if schema is not None:
        data["editorial_schema"] = schema
    data["article_hash"] = _sha256(article)
    claims = []
    if fact_text is not None:
        claim = {
            "id": "C1", "type": "fact", "text": fact_text,
            "article_span": _span(article, fact_text),
            "source_ids": [source["id"]] if source else [],
            "evidence_refs": [],
        }
        if source:
            evidence = source["evidence"]
            claim["evidence_refs"] = [{
                "source_id": source["id"],
                "evidence_sha256": evidence["sha256"],
                "mode": "verbatim",
                "quote": quote if quote is not None else evidence["text"],
            }]
        claims.append(claim)
    data["claims"] = claims

    if blocks is None:
        if claims:
            # 整篇正文是一个连续块（无空行）；fact 块覆盖全块，引用块内 fact 主张。
            blocks = [{"article_span": {"start": 0, "end": len(article)}, "kind": "fact", "claim_ids": ["C1"]}]
        else:
            blocks = [{"article_span": {"start": 0, "end": len(article)}, "kind": "other", "claim_ids": []}]
    data["blocks"] = blocks
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False)


def _assess(decision="pass", blockers=None, notes="", major_issues=None):
    return {"decision": decision, "pass_number": 1,
            "dimensions": {"accuracy": 5, "viewpoint": 5, "usefulness": 5, "voice": 5, "readability": 5},
            "blockers": blockers if blockers is not None else [],
            "major_issues": major_issues if major_issues is not None else [],
            "notes": notes}


def _review(home, run_id, article_version, decision="pass"):
    return call_facade(str(home), "review.build", {
        "requestId": "rv", "runId": run_id, "articleVersion": article_version, "draftVersion": None,
        "assessment": _assess(decision=decision),
    })


def _codes(blockers: list[str]) -> set[str]:
    return {m.group(1) for m in re.finditer(r"\[([A-Z_]+)\]", " ".join(blockers))}


def _verdict_items(report: dict) -> dict[str, dict]:
    notes = report.get("notes", "")
    if not notes.startswith("守卫诊断："):
        return {}
    payload = json.loads(notes[len("守卫诊断："):])
    return {it.get("code"): it for it in payload.get("items", [])}


# ── S：来源直引链 ───────────────────────────────────────────────────────────

def _seed_source_case(home, fact_text: str, evidence_text: str, conclusion_text: str,
                      quote: str | None = None, decision="pass"):
    article = f"{conclusion_text} {fact_text}"
    run_id = create_run(home)
    version = write_article(home, run_id, article)
    source = {"id": "s1", "status": "verified", "url": "https://example.org/report",
              "claim": fact_text, "evidence": {"text": evidence_text,
                                               "sha256": _sha256(evidence_text),
                                               "locator": "第一节"}}
    assert _write_artifact(home, run_id, "sources", _make_sources([source]))["ok"] is True
    assert _write_artifact(home, run_id, "brief",
                           _make_brief(article, takeaway=conclusion_text))["ok"] is True
    assert _write_artifact(home, run_id, "claims",
                           _make_claims(article, fact_text, source, quote=quote))["ok"] is True
    resp = _review(home, run_id, version, decision=decision)
    assert resp["ok"] is True, resp
    return resp["data"]["report"]


def test_s1_opposite_verb_unsupported(tmp_path):
    report = _seed_source_case(make_home(tmp_path), "90% 的用户支持这种方式。",
                               "90% 的用户反对这种方式。", "综上，应采用这种方式。")
    assert report["decision"] == "revise" and report["publishable"] is False
    assert "VERBATIM_CLAIM_MISMATCH" in _codes(report["blockers"])


def test_s2_growth_decline_unsupported(tmp_path):
    report = _seed_source_case(make_home(tmp_path), "90% 的用户增长。",
                               "90% 的用户下降。", "综上，应采用这种方式。")
    assert "VERBATIM_CLAIM_MISMATCH" in _codes(report["blockers"])


def test_s3_rise_fall_unsupported(tmp_path):
    report = _seed_source_case(make_home(tmp_path), "90% 的用户上升。",
                               "90% 的用户下滑。", "综上，应采用这种方式。")
    assert "VERBATIM_CLAIM_MISMATCH" in _codes(report["blockers"])


def test_s4_increase_decrease_unsupported(tmp_path):
    report = _seed_source_case(make_home(tmp_path), "90% 的用户提高。",
                               "90% 的用户降低。", "综上，应采用这种方式。")
    assert "VERBATIM_CLAIM_MISMATCH" in _codes(report["blockers"])


def test_s5_negation_unsupported(tmp_path):
    report = _seed_source_case(make_home(tmp_path), "90% 的用户支持这种方式。",
                               "90% 的用户不支持这种方式。", "综上，应采用这种方式。")
    assert "VERBATIM_CLAIM_MISMATCH" in _codes(report["blockers"])


def test_s6_prefix_negation_unsupported(tmp_path):
    report = _seed_source_case(make_home(tmp_path), "90% 的用户支持这种方式。",
                               "并非90% 的用户支持这种方式。", "综上，应采用这种方式。")
    assert "VERBATIM_CLAIM_MISMATCH" in _codes(report["blockers"])


def test_s7_unrelated_source_unsupported(tmp_path):
    report = _seed_source_case(make_home(tmp_path), "90% 的用户支持这种方式。",
                               "今天天气有变化，用户可查看天气预报。", "综上，应采用这种方式。")
    assert "VERBATIM_CLAIM_MISMATCH" in _codes(report["blockers"])


def test_s8_number_conflict_unsupported(tmp_path):
    report = _seed_source_case(make_home(tmp_path), "90% 的用户支持这种方式。",
                               "35% 的用户支持这种方式。", "综上，应采用这种方式。")
    assert "VERBATIM_CLAIM_MISMATCH" in _codes(report["blockers"])


def test_s9_verbatim_match_supported(tmp_path):
    report = _seed_source_case(make_home(tmp_path), "90% 的用户支持这种方式。",
                               "90% 的用户支持这种方式。", "综上，应采用这种方式。")
    assert report["decision"] == "pass" and report["publishable"] is True
    assert "VERBATIM_CHAIN_VALID" in _verdict_items(report)


def test_s10_truncated_quote_unsupported(tmp_path):
    fact = "90% 的用户支持这种方式。"
    evidence = "并非90% 的用户支持这种方式。"
    report = _seed_source_case(make_home(tmp_path), fact, evidence, "综上，应采用这种方式。",
                               quote=fact)
    assert report["decision"] == "revise"
    assert "VERBATIM_QUOTE_MISMATCH" in _codes(report["blockers"])


def test_s11_synonym_rewrite_unsupported(tmp_path):
    report = _seed_source_case(make_home(tmp_path), "90% 的用户支持这种方式。",
                               "90.0% 的用户支持这种方式。", "综上，应采用这种方式。")
    assert "VERBATIM_CLAIM_MISMATCH" in _codes(report["blockers"])


# ── T：第四条红线（声明存在 + 锚点）──────────────────────────────────────────

def _seed_delivery_case(home, body: str, takeaway: str = "", action: str = "", decision="pass"):
    run_id = create_run(home)
    version = write_article(home, run_id, body)
    assert _write_artifact(home, run_id, "sources", _make_sources([]))["ok"] is True
    assert _write_artifact(home, run_id, "brief",
                           _make_brief(body, takeaway=takeaway, action=action))["ok"] is True
    assert _write_artifact(home, run_id, "claims", _make_claims(body, None, None))["ok"] is True
    resp = _review(home, run_id, version, decision=decision)
    assert resp["ok"] is True, resp
    return resp["data"]["report"]


def test_t1_no_declaration(tmp_path):
    report = _seed_delivery_case(make_home(tmp_path), "因此，情况仍然复杂。")
    assert report["decision"] == "revise" and report["publishable"] is False
    assert "DELIVERY_DECLARATION_REQUIRED" in _codes(report["blockers"])


def test_t2_no_declaration(tmp_path):
    report = _seed_delivery_case(make_home(tmp_path), "所以，事情并不简单。")
    assert "DELIVERY_DECLARATION_REQUIRED" in _codes(report["blockers"])


def test_t3_no_declaration(tmp_path):
    report = _seed_delivery_case(make_home(tmp_path), "综上，情况比较复杂。")
    assert "DELIVERY_DECLARATION_REQUIRED" in _codes(report["blockers"])


def test_t4_no_declaration(tmp_path):
    report = _seed_delivery_case(make_home(tmp_path), "总之，还需继续观察。")
    assert "DELIVERY_DECLARATION_REQUIRED" in _codes(report["blockers"])


def test_t5_no_declaration(tmp_path):
    report = _seed_delivery_case(make_home(tmp_path), "因此，值得我们持续关注。")
    assert "DELIVERY_DECLARATION_REQUIRED" in _codes(report["blockers"])


def test_t6_no_declaration(tmp_path):
    report = _seed_delivery_case(make_home(tmp_path), "各方意见莫衷一是，暂且无法给出答案。")
    assert "DELIVERY_DECLARATION_REQUIRED" in _codes(report["blockers"])


def test_t7_no_declaration(tmp_path):
    report = _seed_delivery_case(make_home(tmp_path), "这个话题争议不断，孰是孰非留待以后再看。")
    assert "DELIVERY_DECLARATION_REQUIRED" in _codes(report["blockers"])


def test_t8_no_declaration(tmp_path):
    report = _seed_delivery_case(make_home(tmp_path), "关于这个问题，有很多看法，相关资料可以在网上找到。")
    assert "DELIVERY_DECLARATION_REQUIRED" in _codes(report["blockers"])


def test_t9_no_declaration(tmp_path):
    report = _seed_delivery_case(make_home(tmp_path), "目前尚无定论，各位读者自行判断。")
    assert "DELIVERY_DECLARATION_REQUIRED" in _codes(report["blockers"])


def test_t10_three_variants(tmp_path):
    body = "关于这个问题，有很多看法。"
    home = make_home(tmp_path)
    r1 = _seed_delivery_case(home, body)
    assert "DELIVERY_DECLARATION_REQUIRED" in _codes(r1["blockers"])
    assert r1["decision"] == "revise" and r1["publishable"] is False

    run_id = create_run(home)
    version = write_article(home, run_id, body)
    assert _write_artifact(home, run_id, "sources", _make_sources([]))["ok"] is True
    assert _write_artifact(home, run_id, "brief", _make_brief(body, takeaway=body))["ok"] is True
    assert _write_artifact(home, run_id, "claims", _make_claims(body, None, None))["ok"] is True
    resp = _review(home, run_id, version, decision="pass")
    r2 = resp["data"]["report"]
    assert "DELIVERY_DECLARED_AND_PRESENT" in _verdict_items(r2)
    assert r2["decision"] == "pass" and r2["publishable"] is True

    run_id2 = create_run(home)
    version2 = write_article(home, run_id2, body)
    assert _write_artifact(home, run_id2, "sources", _make_sources([]))["ok"] is True
    assert _write_artifact(home, run_id2, "brief", _make_brief(body, takeaway=body))["ok"] is True
    assert _write_artifact(home, run_id2, "claims", _make_claims(body, None, None))["ok"] is True
    resp3 = _review(home, run_id2, version2, decision="revise")
    r3 = resp3["data"]["report"]
    assert "DELIVERY_DECLARED_AND_PRESENT" in _verdict_items(r3)
    assert r3["decision"] == "revise" and r3["publishable"] is False


# ── P：正向对照 ─────────────────────────────────────────────────────────────

def test_p1_conclusion_no_connector_passes(tmp_path):
    body = "这条路径值得中小团队优先尝试，三个月内能验证可行性。"
    report = _seed_delivery_case(make_home(tmp_path), body, takeaway=body)
    assert report["decision"] == "pass" and report["publishable"] is True


def test_p4_action_only_passes(tmp_path):
    body = "把预算集中在留存率提升上。"
    report = _seed_delivery_case(make_home(tmp_path), body, action=body)
    assert report["decision"] == "pass" and report["publishable"] is True


# ── 红线一：编造亲历 ───────────────────────────────────────────────────────

def test_fabrication_blocked(tmp_path):
    home = make_home(tmp_path)
    body = "上周我采访了一位行业专家。综上，应继续观察。"
    run_id = create_run(home)
    version = write_article(home, run_id, body)
    assert _write_artifact(home, run_id, "sources", _make_sources([]))["ok"] is True
    assert _write_artifact(home, run_id, "brief", _make_brief(body, takeaway="应继续观察"))["ok"] is True
    assert _write_artifact(home, run_id, "claims", _make_claims(body, None, None))["ok"] is True
    resp = _review(home, run_id, version)
    report = resp["data"]["report"]
    assert report["decision"] == "revise"
    assert "FABRICATED_PERSONAL_EXPERIENCE" in _codes(report["blockers"])


def test_sources_ledger_roundtrip(tmp_path):
    """wewrite_artifact_write kind=sources 真实用例（含 evidence.sha256 补齐）。"""
    home = make_home(tmp_path)
    run_id = create_run(home)
    got = call_facade(str(home), "artifact.get", {"runId": run_id, "kind": "sources"})
    version = got["data"]["version"]
    source = {"id": "s1", "status": "verified", "url": "https://example.com/a",
              "claim": "支持核心事实", "evidence": {"text": "支持核心事实", "locator": "第一节"}}
    resp = _write_artifact(home, run_id, "sources", _make_sources([source]), expected=version)
    assert resp["ok"] is True, resp
    got = call_facade(str(home), "artifact.get", {"runId": run_id, "kind": "sources"})
    data = yaml.safe_load(got["data"]["content"])
    assert data["sources"][0]["evidence"]["sha256"] == _sha256("支持核心事实")
