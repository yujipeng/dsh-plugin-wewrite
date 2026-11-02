"""编辑红线守卫（方案 A 定稿设计 v2）：确定性结构校验，替代文本/词表启发式。

- 来源支持 = 原文直引可验证子集：claim.text 与 evidence.text 严格相等，证据哈希一致，
  定位合法，正文覆盖完整。不做相似度、要素三元组或极性判断。
- 第四条红线 = 明确结论声明存在且落正文（takeaway/action 显式声明 + 正文精确锚点）。
- 三态：supported / unsupported / unknown。unknown 与 unsupported 一律阻断，
  不因任何一条 supported 抵消。
- 红线一（编造亲历/采访/场景）沿用现状。

产物内容契约（editorial_schema: 2）：
- sources 每项新增 evidence.text / evidence.sha256 / evidence.locator。
- claims 新增 article_hash、每条 fact 的 article_span 与 evidence_refs，以及 blocks 覆盖清单。
- brief 新增 delivery.article_hash 与 delivery.anchors。

本模块只产生内部判定项（verdict items），不写盘、不读文件系统。
"""

from __future__ import annotations

import re
from typing import Any

# ── 三态 ────────────────────────────────────────────────────────────────────
SUPPORTED = "supported"
UNSUPPORTED = "unsupported"
UNKNOWN = "unknown"

# 诊断键（全部守卫诊断键，供模型文本清洗用）。
DIAGNOSTIC_CODES = (
    "EVIDENCE_INPUT_REQUIRED",
    "CLAIM_LOCATION_INVALID",
    "EVIDENCE_REFERENCE_INVALID",
    "VERBATIM_QUOTE_MISMATCH",
    "VERBATIM_CLAIM_MISMATCH",
    "VERBATIM_CHAIN_VALID",
    "ARTICLE_COVERAGE_REQUIRED",
    "DELIVERY_DECLARATION_REQUIRED",
    "DELIVERY_LOCATION_INVALID",
    "DELIVERY_TEXT_MISMATCH",
    "DELIVERY_DECLARED_AND_PRESENT",
    "FABRICATED_PERSONAL_EXPERIENCE",
    "FABRICATION_CLEAR",
)

# 模型自由文本清洗保留词（含诊断键 + 结构字段名 + 前缀）。
RESERVED_TOKENS = frozenset(DIAGNOSTIC_CODES) | frozenset({
    "editorial_verdict",
    "facade_editorial_guard",
    "守卫诊断",
})

EDITORIAL_SCHEMA = 2

# 红线一：编造亲历/采访/场景的显式信号。
_FABRICATED_MARKERS = (
    "采访了", "我采访", "接受采访", "我的朋友", "我的同事", "我朋友", "我同事",
    "我的邻居", "我的同学", "亲历", "我亲", "亲眼", "我见过", "我遇到过",
    "那天我", "上周我", "上个月我", "昨天我", "我参加", "我所在", "我们公司", "我们团队",
)

_FACT_TYPE = "fact"
_BLOCK_KINDS = ("fact", "opinion", "other")


def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _as_str(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _sha256(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _is_span_valid(span: Any) -> bool:
    if not isinstance(span, dict):
        return False
    start = span.get("start")
    end = span.get("end")
    if isinstance(start, bool) or isinstance(end, bool):
        return False
    if not isinstance(start, int) or not isinstance(end, int):
        return False
    return start >= 0 and end > start


def _slice_of(article: str, span: dict[str, Any]) -> str | None:
    if not _is_span_valid(span):
        return None
    start = span["start"]
    end = span["end"]
    if start < 0 or end > len(article) or end <= start:
        return None
    return article[start:end]


# ── 正文切块（按空行；码点零基左闭右开）──────────────────────────────────────

def split_blocks(article: str) -> list[dict[str, Any]]:
    """按空行把正文切成连续非空块，返回 [{text,start,end}, ...]。

    - 空行 = 去掉该行空白后为空的行（识别 LF 与 CRLF）；连续空行都是分隔符。
    - 块起止去掉首尾 Unicode 空白，内部原样保留；跨度按原文码点计算（CRLF 占两码点）。
    """
    lines: list[tuple[str, int, int]] = []
    pos = 0
    n = len(article)
    while pos < n:
        start = pos
        j = pos
        while j < n and article[j] not in "\r\n":
            j += 1
        text = article[start:j]
        if j < n:
            if article[j] == "\r" and j + 1 < n and article[j + 1] == "\n":
                end = j + 2
            else:
                end = j + 1
        else:
            end = j
        lines.append((text, start, end))
        pos = end

    groups: list[list[tuple[str, int, int]]] = []
    cur: list[tuple[str, int, int]] = []
    for text, s, e in lines:
        if text.strip() == "":
            if cur:
                groups.append(cur)
                cur = []
        else:
            cur.append((text, s, e))
    if cur:
        groups.append(cur)

    blocks: list[dict[str, Any]] = []
    for g in groups:
        first_start = g[0][1]
        last_end = g[-1][2]
        full = article[first_start:last_end]
        stripped_left = full.lstrip()
        lead = len(full) - len(stripped_left)
        trimmed = stripped_left.rstrip()
        start = first_start + lead
        end = first_start + lead + len(trimmed)
        if not trimmed:
            continue
        blocks.append({"text": trimmed, "start": start, "end": end})
    return blocks


# ── 来源直引链 ──────────────────────────────────────────────────────────────

def _source_entry_by_id(sources: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for s in _as_list(sources.get("sources")):
        if isinstance(s, dict) and s.get("id"):
            out[s.get("id")] = s
    return out


def _source_supported(entry: dict) -> tuple[bool, str | None]:
    """校验单个来源的证据字段；返回 (ok, code)。"""
    evidence = entry.get("evidence")
    if not isinstance(evidence, dict):
        return False, "EVIDENCE_REFERENCE_INVALID"
    text = evidence.get("text")
    if not isinstance(text, str) or not text:
        return False, "EVIDENCE_REFERENCE_INVALID"
    sha = evidence.get("sha256")
    if not isinstance(sha, str) or not re.fullmatch(r"[a-f0-9]{64}", sha):
        return False, "EVIDENCE_REFERENCE_INVALID"
    if sha != _sha256(text):
        return False, "EVIDENCE_REFERENCE_INVALID"
    locator = evidence.get("locator")
    if not isinstance(locator, str) or not locator:
        return False, "EVIDENCE_REFERENCE_INVALID"
    return True, None


def _fact_source_state(
    claim: dict,
    source_by_id: dict[str, dict],
    article: str,
) -> tuple[str, str, str]:
    """判定单个 fact 主张的来源状态。返回 (state, code, reason)。"""
    text = claim.get("text")
    if not isinstance(text, str):
        return UNKNOWN, "EVIDENCE_INPUT_REQUIRED", "fact 主张缺少 text"

    span = claim.get("article_span")
    slice_text = _slice_of(article, span) if isinstance(span, dict) else None
    if slice_text is None or slice_text != text:
        return UNKNOWN, "CLAIM_LOCATION_INVALID", f"主张 {claim.get('id') or '?'} 的定位不合法或正文切片与 text 不一致"

    source_ids = _as_list(claim.get("source_ids"))
    evidence_refs = _as_list(claim.get("evidence_refs"))
    if not source_ids or not evidence_refs:
        return UNKNOWN, "EVIDENCE_REFERENCE_INVALID", f"fact 主张 {claim.get('id') or '?'} 缺少 source_ids 或 evidence_refs"

    refs_by_source: dict[str, list[dict]] = {}
    for ref in evidence_refs:
        if not isinstance(ref, dict):
            return UNKNOWN, "EVIDENCE_REFERENCE_INVALID", f"主张 {claim.get('id') or '?'} 存在非法 evidence_ref"
        refs_by_source.setdefault(ref.get("source_id"), []).append(ref)

    for sid in source_ids:
        if sid not in source_by_id:
            return UNKNOWN, "EVIDENCE_REFERENCE_INVALID", f"主张 {claim.get('id') or '?'} 引用的来源 {sid} 不存在"
        refs = refs_by_source.get(sid)
        if not refs or len(refs) != 1:
            return UNKNOWN, "EVIDENCE_REFERENCE_INVALID", f"主张 {claim.get('id') or '?'} 的来源 {sid} 缺恰一条 evidence_ref"

    for sid in source_ids:
        entry = source_by_id[sid]
        if entry.get("status") != "verified":
            return UNKNOWN, "EVIDENCE_REFERENCE_INVALID", f"来源 {sid} 状态不是 verified"
        ok, code = _source_supported(entry)
        if not ok:
            return UNKNOWN, code, f"来源 {sid} 证据缺失/哈希不符/locator 空"
        ref = refs_by_source[sid][0]
        if ref.get("evidence_sha256") != entry["evidence"]["sha256"]:
            return UNKNOWN, "EVIDENCE_REFERENCE_INVALID", f"来源 {sid} 的 evidence_ref 哈希与来源证据不一致"

    for sid in source_ids:
        entry = source_by_id[sid]
        ref = refs_by_source[sid][0]
        if ref.get("mode") != "verbatim":
            return UNSUPPORTED, "VERBATIM_QUOTE_MISMATCH", f"来源 {sid} 的引用 mode 不是 verbatim"
        quote = ref.get("quote")
        if not isinstance(quote, str) or quote != entry["evidence"]["text"]:
            return UNSUPPORTED, "VERBATIM_QUOTE_MISMATCH", f"来源 {sid} 的 quote 不等于完整证据段"

    for sid in source_ids:
        entry = source_by_id[sid]
        ref = refs_by_source[sid][0]
        if text != ref.get("quote"):
            return UNSUPPORTED, "VERBATIM_CLAIM_MISMATCH", f"主张 {claim.get('id') or '?'} 的 text 与直引不一致"

    return SUPPORTED, "VERBATIM_CHAIN_VALID", f"主张 {claim.get('id') or '?'} 引用链完整"


# ── 第四条红线：声明存在 + 正文锚点 ─────────────────────────────────────────

def _delivery_state(brief: dict, article: str) -> tuple[str, str, str]:
    if brief.get("editorial_schema") != EDITORIAL_SCHEMA:
        return UNKNOWN, "DELIVERY_DECLARATION_REQUIRED", "brief 缺少 editorial_schema: 2 或为旧格式，需补录"

    goal = brief.get("goal")
    if not isinstance(goal, dict):
        return UNKNOWN, "DELIVERY_DECLARATION_REQUIRED", "brief 缺少 goal 声明"
    takeaway = goal.get("takeaway")
    action = goal.get("action")
    if not isinstance(takeaway, str) and not isinstance(action, str):
        return UNKNOWN, "DELIVERY_DECLARATION_REQUIRED", "takeaway/action 缺失或类型错误"
    takeaway = takeaway if isinstance(takeaway, str) else ""
    action = action if isinstance(action, str) else ""
    if not takeaway.strip() and not action.strip():
        return UNKNOWN, "DELIVERY_DECLARATION_REQUIRED", "takeaway 与 action 均为空"

    delivery = brief.get("delivery")
    if not isinstance(delivery, dict):
        return UNKNOWN, "DELIVERY_DECLARATION_REQUIRED", "brief 缺少 delivery 锚点"
    article_hash = delivery.get("article_hash")
    if article_hash != _sha256(article):
        return UNKNOWN, "DELIVERY_LOCATION_INVALID", "delivery.article_hash 与当前正文不一致"

    anchors = _as_list(delivery.get("anchors"))
    by_field: dict[str, list[dict]] = {}
    for a in anchors:
        if isinstance(a, dict) and a.get("field") in ("takeaway", "action"):
            by_field.setdefault(a["field"], []).append(a)

    for field, text in (("takeaway", takeaway), ("action", action)):
        if not text.strip():
            if by_field.get(field):
                return UNKNOWN, "DELIVERY_LOCATION_INVALID", f"空声明 {field} 不得有锚点"
            continue
        field_anchors = by_field.get(field)
        if not field_anchors or len(field_anchors) != 1:
            return UNKNOWN, "DELIVERY_DECLARATION_REQUIRED", f"声明 {field} 必须有且仅有一个锚点"
        anchor = field_anchors[0]
        span = anchor.get("article_span")
        slice_text = _slice_of(article, span) if isinstance(span, dict) else None
        if slice_text is None:
            return UNKNOWN, "DELIVERY_LOCATION_INVALID", f"声明 {field} 的锚点越界或非法"
        if slice_text != text.strip():
            return UNSUPPORTED, "DELIVERY_TEXT_MISMATCH", f"声明 {field} 未落正文或正文文本不一致"

    return SUPPORTED, "DELIVERY_DECLARED_AND_PRESENT", "已声明且已落正文；未验证语义质量"


# ── 红线一：编造亲历/采访/场景 ──────────────────────────────────────────────

def _fabrication_state(article: str, personal_materials_available: bool | None) -> tuple[str, str, str]:
    if not any(m in article for m in _FABRICATED_MARKERS):
        return SUPPORTED, "FABRICATION_CLEAR", "未发现编造亲历/采访/场景信号"
    if personal_materials_available is True:
        return SUPPORTED, "FABRICATION_CLEAR", "有用户提供材料，亲历叙述可追溯"
    return UNSUPPORTED, "FABRICATED_PERSONAL_EXPERIENCE", \
        "正文出现第一人称经历/采访/场景叙述，但没有本次任务用户明确提供的材料来源"


# ── 主入口 ──────────────────────────────────────────────────────────────────

def compute_verdict(
    *,
    article: str,
    brief: dict,
    claims: dict,
    sources: dict,
    personal_materials_available: bool | None = None,
) -> dict[str, Any]:
    """计算编辑红线判定项列表。返回 {items: [...]}，每条含 rule/id/state/code/reason。"""
    items: list[dict[str, Any]] = []
    claims_data = claims if isinstance(claims, dict) else {}
    sources_data = sources if isinstance(sources, dict) else {}
    brief_data = brief if isinstance(brief, dict) else {}

    # 红线一：编造亲历。
    fstate, fcode, freason = _fabrication_state(article, personal_materials_available)
    items.append({"rule": "fabrication", "id": None, "state": fstate, "code": fcode, "reason": freason})

    # 第四条：声明存在 + 锚点。
    dstate, dcode, dreason = _delivery_state(brief_data, article)
    items.append({"rule": "delivery", "id": None, "state": dstate, "code": dcode, "reason": dreason})

    # 来源输入：claims/sources 必须是 schema 2。
    if claims_data.get("editorial_schema") != EDITORIAL_SCHEMA or sources_data.get("editorial_schema") != EDITORIAL_SCHEMA:
        items.append({"rule": "source_input", "id": None, "state": UNKNOWN,
                      "code": "EVIDENCE_INPUT_REQUIRED",
                      "reason": "claims 或 sources 缺少 editorial_schema: 2 或为旧格式，需补录"})
    else:
        claim_list = _as_list(claims_data.get("claims"))
        blocks = _as_list(claims_data.get("blocks"))
        source_by_id = _source_entry_by_id(sources_data)
        articles_hash = claims_data.get("article_hash")

        if articles_hash != _sha256(article):
            items.append({"rule": "source_input", "id": None, "state": UNKNOWN,
                          "code": "CLAIM_LOCATION_INVALID",
                          "reason": "claims.article_hash 与当前正文不一致"})
        else:
            items.append({"rule": "source_input", "id": None, "state": SUPPORTED,
                          "code": "VERBATIM_CHAIN_VALID", "reason": "claims/sources 为 schema 2 且正文哈希一致"})

        # 逐 fact 判定。
        facts = [c for c in claim_list if isinstance(c, dict) and c.get("type") == _FACT_TYPE]
        if not facts:
            items.append({"rule": "source_fact", "id": None, "state": SUPPORTED,
                          "code": "VERBATIM_CHAIN_VALID",
                          "reason": "无已声明事实，引用检查不适用"})
        else:
            for c in facts:
                st, code, reason = _fact_source_state(c, source_by_id, article)
                items.append({"rule": "source_fact", "id": c.get("id"), "state": st, "code": code, "reason": reason})

        coverage_state = _coverage_state(article, claims_data, facts, source_by_id)
        items.append(coverage_state)

    return {"items": items}


def _coverage_state(article: str, claims: dict, facts: list[dict], source_by_id: dict[str, dict]) -> dict[str, Any]:
    """正文按空行切块，claims.blocks 必须一一覆盖。"""
    blocks = _as_list(claims.get("blocks"))
    expected = split_blocks(article)
    expected_spans = [(b["start"], b["end"]) for b in expected]

    got_spans: list[tuple[int, int]] = []
    for b in blocks:
        if not isinstance(b, dict):
            return {"rule": "article_coverage", "id": None, "state": UNKNOWN,
                    "code": "ARTICLE_COVERAGE_REQUIRED", "reason": "blocks 存在非法条目"}
        span = b.get("article_span")
        if not _is_span_valid(span):
            return {"rule": "article_coverage", "id": None, "state": UNKNOWN,
                    "code": "ARTICLE_COVERAGE_REQUIRED", "reason": "blocks 存在非法 article_span"}
        got_spans.append((span["start"], span["end"]))

    if got_spans != expected_spans:
        return {"rule": "article_coverage", "id": None, "state": UNKNOWN,
                "code": "ARTICLE_COVERAGE_REQUIRED",
                "reason": "blocks 未按顺序覆盖全部正文块（存在遗漏/重复/额外块）"}

    fact_by_id = {c.get("id"): c for c in facts if isinstance(c, dict)}
    for b, (start, end) in zip(blocks, expected_spans):
        kind = b.get("kind")
        if kind not in _BLOCK_KINDS:
            return {"rule": "article_coverage", "id": None, "state": UNKNOWN,
                    "code": "ARTICLE_COVERAGE_REQUIRED", "reason": f"block 的 kind 不合法：{kind}"}
        cids = _as_list(b.get("claim_ids"))
        if kind == "fact":
            if not cids:
                return {"rule": "article_coverage", "id": None, "state": UNKNOWN,
                        "code": "ARTICLE_COVERAGE_REQUIRED", "reason": "fact 块缺少 claim_ids"}
            for cid in cids:
                claim = fact_by_id.get(cid)
                if claim is None:
                    return {"rule": "article_coverage", "id": None, "state": UNKNOWN,
                            "code": "ARTICLE_COVERAGE_REQUIRED", "reason": f"fact 块引用未登记的主张 {cid}"}
                span = claim.get("article_span")
                if not _is_span_valid(span) or not (start <= span["start"] and span["end"] <= end):
                    return {"rule": "article_coverage", "id": None, "state": UNKNOWN,
                            "code": "ARTICLE_COVERAGE_REQUIRED", "reason": f"主张 {cid} 跨块或不在本块内"}
        else:
            if cids:
                return {"rule": "article_coverage", "id": None, "state": UNKNOWN,
                        "code": "ARTICLE_COVERAGE_REQUIRED", "reason": f"{kind} 块不得有 claim_ids"}

    return {"rule": "article_coverage", "id": None, "state": SUPPORTED,
            "code": "VERBATIM_CHAIN_VALID", "reason": "正文覆盖完整"}


def has_blocking(verdict: dict[str, Any]) -> bool:
    """任一未知/不支持即阻断。"""
    for item in verdict.get("items", []):
        if item.get("state") in (UNKNOWN, UNSUPPORTED):
            return True
    return False


def guard_blockers(verdict: dict[str, Any]) -> list[str]:
    """把 unknown/unsupported 的判定项转成中文 blocker 文本。"""
    out: list[str] = []
    for item in verdict.get("items", []):
        if item.get("state") in (UNKNOWN, UNSUPPORTED):
            out.append(f"[{item.get('code')}] {item.get('reason')}")
    return out


# ── 模型自由文本清洗 ────────────────────────────────────────────────────────

def scrub_reserved(text: str) -> str:
    """把模型自由文本中的守卫保留词（诊断键/结构字段名/前缀）替换为中文占位。

    不区分大小写匹配 ASCII 保留词；中文前缀按字面匹配。清洗只改文本，不删除条目。
    """
    if not isinstance(text, str):
        return text
    out = text
    for token in sorted(RESERVED_TOKENS, key=len, reverse=True):
        if token in ("守卫诊断",):
            out = out.replace(token, "模型自报诊断标记（未采用）")
        else:
            # ASCII 保留词不区分大小写。
            pattern = re.compile(re.escape(token), re.IGNORECASE)
            out = pattern.sub("模型自报诊断标记（未采用）", out)
    return out


def scrub_assessment(assessment: dict[str, Any]) -> dict[str, Any]:
    """复制 assessment，对 notes、blockers 每项、major_issues 每项做保留词清洗。

    清洗只改自由文本，不删除 blocker 条目、不改变 decision/分数/pass_number；
    替换后空字符串用「模型提出阻断意见，需复核」补齐。
    """
    out = dict(assessment) if isinstance(assessment, dict) else {}
    if isinstance(out.get("notes"), str):
        out["notes"] = scrub_reserved(out["notes"])
    for field in ("blockers", "major_issues"):
        if isinstance(out.get(field), list):
            cleaned: list[str] = []
            for item in out[field]:
                if isinstance(item, str):
                    s = scrub_reserved(item)
                    cleaned.append(s if s.strip() else "模型提出阻断意见，需复核")
                else:
                    cleaned.append(item)
            out[field] = cleaned
    return out
