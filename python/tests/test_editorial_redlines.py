"""方案 A 负向用例：编辑红线守卫（editorial-quality.md 的「不可交付」红线）。

审稿 build_report 之上叠加确定性拦截：正文/sources/claims/brief 出现可判定的红线信号时，
无论模型自报 decision/blockers 如何，report.decision 都必须降级为 revise、publishable 必须 false。
"""

from __future__ import annotations

import yaml

import pytest

from conftest import call_facade, create_run, make_home, write_article
from dsh_wewrite_facade.editorial import (
    GUARD_NO_CONCLUSION,
    GUARD_UNSOURCED_FACT,
    editorial_blockers,
)


def _write_artifact(home, run_id, kind, content, expected=None):
    if expected is None:
        got = call_facade(str(home), "artifact.get", {"runId": run_id, "kind": kind})
        expected = got["data"]["version"] if got.get("ok") is True else None
    return call_facade(str(home), "artifact.write", {
        "requestId": f"w-{kind}", "runId": run_id, "kind": kind,
        "content": content, "expectedVersion": expected,
    })


def _claims_with_fact_unsourced() -> str:
    return yaml.safe_dump({
        "version": 1,
        "claims": [
            {"id": "C1", "text": "核心事实主张", "type": "fact", "source_ids": [],
             "status": "unsupported", "boundary": ""},
        ],
    }, allow_unicode=True, sort_keys=False)


def _sources_empty() -> str:
    return yaml.safe_dump({"version": 1, "run_id": "r", "sources": []}, allow_unicode=True, sort_keys=False)


def _sources_verified() -> str:
    return yaml.safe_dump({
        "version": 1, "run_id": "r",
        "sources": [{"id": "s1", "title": "官方", "publisher": "p", "url": "https://example.com/a",
                     "published_at": "2026-01-01", "claim": "支持主张", "status": "verified"}],
    }, allow_unicode=True, sort_keys=False)


def _sources_unrelated_verified() -> str:
    """一条 verified 来源，但主题与正文断言无关（讲天气）。"""
    return yaml.safe_dump({
        "version": 1, "run_id": "r",
        "sources": [{"id": "s2", "title": "气象局", "publisher": "气象局",
                     "url": "https://example.com/weather",
                     "published_at": "2026-01-01", "claim": "今天局部地区有阵雨", "status": "verified"}],
    }, allow_unicode=True, sort_keys=False)


def _brief(personal_available: bool) -> str:
    return yaml.safe_dump({
        "version": 1,
        "audience": {"who": "读者", "context": "场景", "question": "问题"},
        "goal": {"takeaway": "结论", "action": "行动"},
        "thesis": {"statement": "判断", "novelty": "新", "boundary": "边界", "counterpoint": "反方"},
        "personal_materials": {"available": personal_available, "items": []},
        "framework": "观点",
        "sections": [],
        "constraints": {"desired_length": "1200-2500 字", "must_include": [], "must_avoid": []},
    }, allow_unicode=True, sort_keys=False)


def _assess(decision="pass", blockers=None):
    return {"decision": decision, "pass_number": 1,
            "dimensions": {"accuracy": 5, "viewpoint": 5, "usefulness": 5, "voice": 5, "readability": 5},
            "blockers": blockers if blockers is not None else [], "major_issues": [], "notes": "ok"}


def _review(home, run_id, article_version):
    return call_facade(str(home), "review.build", {
        "requestId": "rv", "runId": run_id, "articleVersion": article_version, "draftVersion": None,
        "assessment": _assess(),
    })


def test_unsourced_core_fact_must_revise(tmp_path):
    """负向 1：无来源的核心事实 → 审稿必须判 revise，不能 pass。"""
    home = make_home(tmp_path)
    run_id = create_run(home)
    article = "研究表明，90% 的用户会更喜欢这种方式。"
    version = write_article(home, run_id, article)
    assert _write_artifact(home, run_id, "sources", _sources_empty())["ok"] is True
    assert _write_artifact(home, run_id, "claims", _claims_with_fact_unsourced())["ok"] is True
    resp = _review(home, run_id, version)
    assert resp["ok"] is True, resp
    report = resp["data"]["report"]
    assert report["decision"] == "revise"
    assert report["publishable"] is False
    assert any("无来源" in b for b in report["blockers"])


def test_fabricated_personal_experience_blocked(tmp_path):
    """负向 2：编造亲历/采访/场景 → 审稿必须拦截。"""
    home = make_home(tmp_path)
    run_id = create_run(home)
    article = "上周我采访了一位行业专家，我的朋友也告诉我同样的结论。"
    version = write_article(home, run_id, article)
    assert _write_artifact(home, run_id, "sources", _sources_empty())["ok"] is True
    assert _write_artifact(home, run_id, "brief", _brief(personal_available=False))["ok"] is True
    resp = _review(home, run_id, version)
    assert resp["ok"] is True, resp
    report = resp["data"]["report"]
    assert report["decision"] == "revise"
    assert report["publishable"] is False
    assert any("编造" in b for b in report["blockers"])


def test_claim_evidence_disconnect_blocked(tmp_path):
    """负向 3：论点与证据脱节（fact 主张无 source_ids）→ 必须拦截。"""
    home = make_home(tmp_path)
    run_id = create_run(home)
    article = "本文给出一个可迁移的判断框架。"
    version = write_article(home, run_id, article)
    assert _write_artifact(home, run_id, "sources", _sources_verified())["ok"] is True
    assert _write_artifact(home, run_id, "claims", _claims_with_fact_unsourced())["ok"] is True
    resp = _review(home, run_id, version)
    assert resp["ok"] is True, resp
    report = resp["data"]["report"]
    assert report["decision"] == "revise"
    assert report["publishable"] is False
    assert any("脱节" in b for b in report["blockers"])


def test_second_round_still_blocked(tmp_path):
    """负向 4：第二轮仍不过 → 必须保持 revise，不能伪造 pass。"""
    home = make_home(tmp_path)
    run_id = create_run(home)
    article = "研究表明，90% 的用户会更喜欢这种方式。"
    version = write_article(home, run_id, article)
    assert _write_artifact(home, run_id, "sources", _sources_empty())["ok"] is True
    assert _write_artifact(home, run_id, "claims", _claims_with_fact_unsourced())["ok"] is True
    resp = call_facade(str(home), "review.build", {
        "requestId": "rv2", "runId": run_id, "articleVersion": version, "draftVersion": None,
        "assessment": _assess(decision="pass", blockers=[]),
    })
    assert resp["ok"] is True, resp
    report = resp["data"]["report"]
    # 模型第二轮仍自报 pass，但红线守卫必须强制 revise（缩小承诺路径由 skill 指引）。
    assert report["decision"] == "revise"
    assert report["publishable"] is False


def test_sources_ledger_roundtrip(tmp_path):
    """验收：wewrite_artifact_write kind=sources 有真实用例——写入来源账本、sources 产物非空、run/detail 可读到。"""
    home = make_home(tmp_path)
    run_id = create_run(home)
    # 读当前 sources 版本（create_run 预建空账本）。
    resp = call_facade(str(home), "artifact.get", {"runId": run_id, "kind": "sources"})
    assert resp["ok"] is True, resp
    version = resp["data"]["version"]
    ledger = yaml.safe_dump({
        "version": 1, "run_id": run_id,
        "sources": [
            {"id": "s1", "title": "官方文档", "publisher": "官方", "url": "https://example.com/a",
             "published_at": "2026-01-01", "claim": "支持核心事实", "status": "verified"},
        ],
    }, allow_unicode=True, sort_keys=False)
    resp = _write_artifact(home, run_id, "sources", ledger, expected=version)
    assert resp["ok"] is True, resp
    # run/detail 中 sources 产物非空，且内容可读回。
    detail = call_facade(str(home), "run.detail", {"runId": run_id})
    assert detail["ok"] is True, detail
    sources_artifact = [a for a in detail["data"]["artifacts"] if a["kind"] == "sources"][0]
    assert sources_artifact["exists"] is True
    assert sources_artifact["nonEmpty"] is True
    got = call_facade(str(home), "artifact.get", {"runId": run_id, "kind": "sources"})
    assert got["ok"] is True
    assert "verified" in got["data"]["content"]


def test_unrelated_source_cannot_pass(tmp_path):
    """返修 1：来源不支持正文断言 → 必须 revise，不能 pass。

    正文「研究表明，90% 的用户支持」+ 一条讲天气的 verified 来源，
    旧实现只看「有没有 verified」→ 误判 pass；现在按断言与来源相关性判 revise。
    """
    home = make_home(tmp_path)
    run_id = create_run(home)
    article = "研究表明，90% 的用户支持这种方式。"
    version = write_article(home, run_id, article)
    assert _write_artifact(home, run_id, "sources", _sources_unrelated_verified())["ok"] is True
    assert _write_artifact(home, run_id, "claims", _claims_with_fact_unsourced())["ok"] is True
    resp = _review(home, run_id, version)
    assert resp["ok"] is True, resp
    report = resp["data"]["report"]
    assert report["decision"] == "revise"
    assert report["publishable"] is False
    assert report["blockers"], "必须有非空 blockers"
    assert any("不支持" in b or "无来源" in b for b in report["blockers"])


def test_unrelated_source_guard_off_fails(tmp_path):
    """返修 1 反证：关掉 GUARD_UNSOURCED_FACT 后，上述场景应回到旧实现（误判为无阻断）。"""
    home = make_home(tmp_path)
    run_id = create_run(home)
    article = "研究表明，90% 的用户支持这种方式。"
    _ = home, run_id, article
    # 直接对判定函数做反证：有无关 verified 来源时，打开守卫有阻断，关掉守卫无阻断。
    unrelated = yaml.safe_load(_sources_unrelated_verified())
    on = editorial_blockers(article=article, claims={"claims": []}, sources=unrelated)
    off = editorial_blockers(
        article=article, claims={"claims": []}, sources=unrelated,
        disabled={GUARD_UNSOURCED_FACT},
    )
    assert any("不支持" in b or "无来源" in b for b in on)
    assert off == [], "关掉守卫应退回旧行为（无阻断），反证才成立"


def test_no_conclusion_blocked(tmp_path):
    """返修 2：第四条红线（无结论/行动/判断方法）→ 必须 revise。"""
    home = make_home(tmp_path)
    run_id = create_run(home)
    article = "关于这个问题，有很多看法。"
    version = write_article(home, run_id, article)
    assert _write_artifact(home, run_id, "sources", _sources_empty())["ok"] is True
    resp = _review(home, run_id, version)
    assert resp["ok"] is True, resp
    report = resp["data"]["report"]
    assert report["decision"] == "revise"
    assert report["publishable"] is False
    assert any("结论" in b or "行动" in b or "判断方法" in b for b in report["blockers"])


def test_no_conclusion_guard_off_fails(tmp_path):
    """返修 2 反证：关掉 GUARD_NO_CONCLUSION 后，无结论正文应不再命中第四条红线。"""
    article = "关于这个问题，有很多看法。"
    on = editorial_blockers(article=article, claims={"claims": []}, sources={"sources": []})
    off = editorial_blockers(
        article=article, claims={"claims": []}, sources={"sources": []},
        disabled={GUARD_NO_CONCLUSION},
    )
    assert any("结论" in b or "行动" in b or "判断方法" in b for b in on)
    assert off == [], "关掉守卫应不再命中第四条红线，反证才成立"


def test_clean_article_with_sources_passes(tmp_path):
    """正向对照：有 verified 来源、无红线信号时，pass 不被误伤。"""
    home = make_home(tmp_path)
    run_id = create_run(home)
    article = "官方文档给出了明确的行为定义，读者可以据此判断。"
    version = write_article(home, run_id, article)
    assert _write_artifact(home, run_id, "sources", _sources_verified())["ok"] is True
    claims = yaml.safe_dump({
        "version": 1,
        "claims": [
            {"id": "C1", "text": "主张", "type": "fact", "source_ids": ["s1"],
             "status": "supported", "boundary": ""},
        ],
    }, allow_unicode=True, sort_keys=False)
    assert _write_artifact(home, run_id, "claims", claims)["ok"] is True
    resp = _review(home, run_id, version)
    assert resp["ok"] is True, resp
    report = resp["data"]["report"]
    assert report["decision"] == "pass"
    assert report["publishable"] is True
