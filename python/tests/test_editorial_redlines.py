"""方案 A 负向用例（返修 3）：编辑红线守卫（editorial-quality.md 的「不可交付」红线）。

全部走完整 review.build 链路（真实 facade 子进程），不直接调 editorial_blockers。
覆盖：来源不支持断言（含数值冲突）、第四条红线（无明确结论）、以及保留正向对照。
"""

from __future__ import annotations

import yaml

from conftest import call_facade, create_run, make_home, write_article


def _write_artifact(home, run_id, kind, content, expected=None):
    if expected is None:
        got = call_facade(str(home), "artifact.get", {"runId": run_id, "kind": kind})
        expected = got["data"]["version"] if got.get("ok") is True else None
    return call_facade(str(home), "artifact.write", {
        "requestId": f"w-{kind}", "runId": run_id, "kind": kind,
        "content": content, "expectedVersion": expected,
    })


def _sources(items):
    return yaml.safe_dump({"version": 1, "run_id": "r", "sources": items},
                          allow_unicode=True, sort_keys=False)


def _source(status="verified", claim="", title="", sid="s1", url="https://example.com/a"):
    return {"id": sid, "title": title, "publisher": "p", "url": url,
            "published_at": "2026-01-01", "claim": claim, "status": status}


def _assess(decision="pass", blockers=None):
    return {"decision": decision, "pass_number": 1,
            "dimensions": {"accuracy": 5, "viewpoint": 5, "usefulness": 5, "voice": 5, "readability": 5},
            "blockers": blockers if blockers is not None else [], "major_issues": [], "notes": "ok"}


def _review(home, run_id, article_version):
    return call_facade(str(home), "review.build", {
        "requestId": "rv", "runId": run_id, "articleVersion": article_version, "draftVersion": None,
        "assessment": _assess(),
    })


def _seed(home, article, sources_items=None, fact_text=None, fact_source_ids=None):
    """构造 run + article + sources + claims（fact 引用来源），返回 (run_id, article_version)。"""
    run_id = create_run(home)
    version = write_article(home, run_id, article)
    if sources_items is not None:
        assert _write_artifact(home, run_id, "sources", _sources(sources_items))["ok"] is True
    if fact_text is not None:
        claims = yaml.safe_dump({
            "version": 1,
            "claims": [{"id": "C1", "text": fact_text, "type": "fact",
                        "source_ids": fact_source_ids if fact_source_ids is not None else [],
                        "status": "supported", "boundary": ""}],
        }, allow_unicode=True, sort_keys=False)
        assert _write_artifact(home, run_id, "claims", claims)["ok"] is True
    return run_id, version


# ── 返修要求 1：来源支持必须是「断言要素被覆盖」 ─────────────────────────────

def test_unrelated_source_cannot_pass(tmp_path):
    """回归 1：天气 verified 来源 + 90% 用户断言 + fact 引用该来源 → revise。"""
    home = make_home(tmp_path)
    run_id, version = _seed(
        home,
        "研究表明，90% 的用户支持这种方式。",
        sources_items=[_source(claim="今天局部地区有阵雨", title="气象局")],
        fact_text="90% 的用户支持这种方式",
        fact_source_ids=["s1"],
    )
    resp = _review(home, run_id, version)
    assert resp["ok"] is True, resp
    report = resp["data"]["report"]
    assert report["decision"] == "revise"
    assert report["publishable"] is False
    assert report["blockers"], "必须有非空 blockers"
    assert any("不支持" in b or "无来源" in b for b in report["blockers"])


def test_number_conflict_source_cannot_pass(tmp_path):
    """回归 2：来源 claim 数字冲突（90% vs 35%）→ revise，必须抓数值不一致。"""
    home = make_home(tmp_path)
    run_id, version = _seed(
        home,
        "研究表明，90% 的用户支持这种方式。",
        sources_items=[_source(claim="调研显示约 35% 的用户支持这种新型订阅方式", title="调研报告")],
        fact_text="90% 的用户支持这种方式",
        fact_source_ids=["s1"],
    )
    resp = _review(home, run_id, version)
    assert resp["ok"] is True, resp
    report = resp["data"]["report"]
    assert report["decision"] == "revise"
    assert report["publishable"] is False
    assert report["blockers"], "必须有非空 blockers"


# ── 返修要求 2：第四条红线（明确结论必须存在） ──────────────────────────────

def test_no_conclusion_mozhongyishi(tmp_path):
    """回归 3：「各方意见莫衷一是，暂且无法给出答案。」→ revise。"""
    home = make_home(tmp_path)
    run_id, version = _seed(home, "各方意见莫衷一是，暂且无法给出答案。")
    resp = _review(home, run_id, version)
    assert resp["ok"] is True, resp
    report = resp["data"]["report"]
    assert report["decision"] == "revise"
    assert report["publishable"] is False
    assert any("结论" in b or "行动" in b or "判断方法" in b for b in report["blockers"])


def test_no_conclusion_liudai(tmp_path):
    """回归 4：「这个话题争议不断，孰是孰非留待以后再看。」→ revise。"""
    home = make_home(tmp_path)
    run_id, version = _seed(home, "这个话题争议不断，孰是孰非留待以后再看。")
    resp = _review(home, run_id, version)
    assert resp["ok"] is True, resp
    report = resp["data"]["report"]
    assert report["decision"] == "revise"
    assert report["publishable"] is False


def test_no_conclusion_can_online(tmp_path):
    """回归 5a：「有很多看法，相关资料可以在网上找到。」→ revise（宽泛「可以」不再豁免）。"""
    home = make_home(tmp_path)
    run_id, version = _seed(home, "关于这个问题，有很多看法，相关资料可以在网上找到。")
    resp = _review(home, run_id, version)
    assert resp["ok"] is True, resp
    report = resp["data"]["report"]
    assert report["decision"] == "revise"
    assert report["publishable"] is False


def test_no_conclusion_zixing_panduan(tmp_path):
    """回归 5b：「目前尚无定论，各位读者自行判断。」→ revise（宽泛「判断」不再豁免）。"""
    home = make_home(tmp_path)
    run_id, version = _seed(home, "目前尚无定论，各位读者自行判断。")
    resp = _review(home, run_id, version)
    assert resp["ok"] is True, resp
    report = resp["data"]["report"]
    assert report["decision"] == "revise"
    assert report["publishable"] is False


# ── 正向对照：合规文章不得被误伤 ─────────────────────────────────────────────

def test_positive_number_match_passes(tmp_path):
    """正向：正文 90% 与来源 90% 同主体 → 仍 pass。"""
    home = make_home(tmp_path)
    run_id, version = _seed(
        home,
        "研究表明，90% 的用户支持这种方式。综上，应继续采用该方式。",
        sources_items=[_source(claim="约 90% 的用户支持这种方式")],
        fact_text="90% 的用户支持这种方式",
        fact_source_ids=["s1"],
    )
    resp = _review(home, run_id, version)
    assert resp["ok"] is True, resp
    report = resp["data"]["report"]
    assert report["decision"] == "pass"
    assert report["publishable"] is True


def test_positive_conclusion_with_source_passes(tmp_path):
    """正向：给出明确结论 + 有匹配来源 → 仍 pass。"""
    home = make_home(tmp_path)
    run_id, version = _seed(
        home,
        "综上，本文认为应选择方案 A。",
        sources_items=[_source(claim="应选择方案 A")],
        fact_text="应选择方案 A",
        fact_source_ids=["s1"],
    )
    resp = _review(home, run_id, version)
    assert resp["ok"] is True, resp
    report = resp["data"]["report"]
    assert report["decision"] == "pass"
    assert report["publishable"] is True


# ── 保留的既有负向用例 ──────────────────────────────────────────────────────

def test_unsourced_core_fact_must_revise(tmp_path):
    """负向：无来源的核心事实 → revise。"""
    home = make_home(tmp_path)
    run_id, version = _seed(
        home,
        "研究表明，90% 的用户会更喜欢这种方式。",
        sources_items=[],
        fact_text="核心事实主张",
        fact_source_ids=[],
    )
    resp = _review(home, run_id, version)
    assert resp["ok"] is True, resp
    report = resp["data"]["report"]
    assert report["decision"] == "revise"
    assert report["publishable"] is False
    assert any("无来源" in b for b in report["blockers"])


def test_fabricated_personal_experience_blocked(tmp_path):
    """负向：编造亲历/采访/场景 → 拦截。"""
    home = make_home(tmp_path)
    run_id = create_run(home)
    article = "上周我采访了一位行业专家，我的朋友也告诉我同样的结论。"
    version = write_article(home, run_id, article)
    assert _write_artifact(home, run_id, "sources", _sources([]))["ok"] is True
    brief = yaml.safe_dump({
        "version": 1,
        "audience": {"who": "读者", "context": "场景", "question": "问题"},
        "goal": {"takeaway": "结论", "action": "行动"},
        "thesis": {"statement": "判断", "novelty": "新", "boundary": "边界", "counterpoint": "反方"},
        "personal_materials": {"available": False, "items": []},
        "framework": "观点", "sections": [],
        "constraints": {"desired_length": "1200-2500 字", "must_include": [], "must_avoid": []},
    }, allow_unicode=True, sort_keys=False)
    assert _write_artifact(home, run_id, "brief", brief)["ok"] is True
    resp = _review(home, run_id, version)
    assert resp["ok"] is True, resp
    report = resp["data"]["report"]
    assert report["decision"] == "revise"
    assert report["publishable"] is False
    assert any("编造" in b for b in report["blockers"])


def test_claim_evidence_disconnect_blocked(tmp_path):
    """负向：fact 主张无 source_ids → 拦截（论点与证据脱节）。"""
    home = make_home(tmp_path)
    run_id, version = _seed(
        home,
        "本文给出一个可迁移的判断框架。",
        sources_items=[_source(claim="支持主张")],
        fact_text="核心事实主张",
        fact_source_ids=[],
    )
    resp = _review(home, run_id, version)
    assert resp["ok"] is True, resp
    report = resp["data"]["report"]
    assert report["decision"] == "revise"
    assert report["publishable"] is False
    assert any("脱节" in b for b in report["blockers"])


def test_sources_ledger_roundtrip(tmp_path):
    """验收：wewrite_artifact_write kind=sources 真实用例——写入来源账本、run/detail 可读。"""
    home = make_home(tmp_path)
    run_id = create_run(home)
    got = call_facade(str(home), "artifact.get", {"runId": run_id, "kind": "sources"})
    version = got["data"]["version"]
    ledger = yaml.safe_dump({
        "version": 1, "run_id": run_id,
        "sources": [_source(sid="s1", claim="支持核心事实", title="官方文档")],
    }, allow_unicode=True, sort_keys=False)
    resp = _write_artifact(home, run_id, "sources", ledger, expected=version)
    assert resp["ok"] is True, resp
    detail = call_facade(str(home), "run.detail", {"runId": run_id})
    assert detail["ok"] is True, detail
    sources_artifact = [a for a in detail["data"]["artifacts"] if a["kind"] == "sources"][0]
    assert sources_artifact["exists"] is True
    assert sources_artifact["nonEmpty"] is True
    got = call_facade(str(home), "artifact.get", {"runId": run_id, "kind": "sources"})
    assert got["ok"] is True
    assert "verified" in got["data"]["content"]
