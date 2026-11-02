"""编辑红线守卫（方案 A 返修）：审稿时按 editorial-quality.md 的「不可交付」红线做确定性拦截。

- 不替代模型编辑判断，只做「伪 pass 拦截」：正文/sources/claims 出现可判定的红线信号时，
  无论模型自报的 decision/blocks 如何，一律降级为 revise 并返回阻断项。
- 判定全部基于产物文本与来源账本（sources.yaml）、主张清单（claims.yaml）与任务书
  （brief.yaml 的 personal_materials.available），不依赖模型自评分数。
- 覆盖四条红线：
  1. 编造亲历/采访/场景；
  2. 核心具体事实没有来源（含「来源存在但不支持正文说法」）；
  3. 论点与证据脱节（source_ids 必须解析到真实 verified 来源，且来源 claim 与主张相关）；
  4. 文章没有给目标读者明确的结论、行动或判断方法。
- 每条判定带可关闭开关（`disabled` 集合），供反证用例演示「关掉逻辑 → 用例 FAIL」。
"""

from __future__ import annotations

import re
from typing import Any, Iterable

# 四条红线的开关名（反证用例用）。
GUARD_FABRICATION = "fabrication"
GUARD_UNSOURCED_FACT = "unsourced_fact"
GUARD_DISCONNECT = "disconnect"
GUARD_NO_CONCLUSION = "no_conclusion"
GUARD_NAMES = (GUARD_FABRICATION, GUARD_UNSOURCED_FACT, GUARD_DISCONNECT, GUARD_NO_CONCLUSION)

# 编造亲历/采访/场景的显式信号（第一人称事件/访谈/场景标记）。
_FABRICATED_MARKERS = (
    "采访了",
    "我采访",
    "接受采访",
    "我的朋友",
    "我的同事",
    "我朋友",
    "我同事",
    "我的邻居",
    "我的同学",
    "亲历",
    "我亲",
    "亲眼",
    "我见过",
    "我遇到过",
    "那天我",
    "上周我",
    "上个月我",
    "昨天我",
    "我参加",
    "我所在",
    "我们公司",
    "我们团队",
)

# 核心具体事实的显式信号（数字/百分比/引用/研究/数据/统计/调查/报道 等需要来源支撑的说法）。
_FACT_MARKERS = (
    "研究表明",
    "研究显示",
    "数据表明",
    "数据显示",
    "据统计",
    "调查显示",
    "调查发现",
    "报告显示",
    "报告指出",
    "官方数据显示",
    "实验证明",
    "测评显示",
    "％",
    "%",
)

# 数字断言信号：数字 + 比例/倍数/百分点等量级单位（不含年份等弱信号）。
_NUMBER_ASSERTION_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:%|％|倍|成|个百分点)")

# 空泛收尾信号（红线四：没有明确结论/行动/判断方法的空泛表述）。
_EMPTY_HEDGE_MARKERS = (
    "有很多看法",
    "众说纷纭",
    "没有定论",
    "难以判断",
    "见仁见智",
    "仁者见仁",
    "不好说",
    "各有各的",
    "看法不一",
)

# 结论/行动/判断方法的正向信号（只要出现其一，即不判「无结论」）。
_CONCLUSION_MARKERS = (
    "结论",
    "综上",
    "总之",
    "因此",
    "所以",
    "应该",
    "建议",
    "判断",
    "取决于",
    "选择",
    "标准",
    "方法",
    "步骤",
    "首先",
    "其次",
    "才能",
    "需要",
    "可以",
    "不妨",
    "关键在于",
    "核心是",
    "最重要的是",
    "我认为",
    "行动",
)

# fact 主张类型（对应 article-brief.md：fact 必须有能直接支持它的来源）。
_FACT_TYPE = "fact"

# 文本归一化时剔除的常见虚词（保证「90% 用户支持」与「90% 的用户支持」可对齐）。
_STOP_CHARS = set("的了是在对有与和之地得吗呢么又很也就还着过把被让")


def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _normalize(text: str) -> str:
    """归一化文本：去空白/标点、统一小写、剔除常见虚词（保留 CJK 与字母数字）。"""
    cleaned = re.sub(r"[\s\W_]+", "", text, flags=re.UNICODE).lower()
    return "".join(ch for ch in cleaned if ch not in _STOP_CHARS)


_CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]{2,}")
_ALNUM_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._%\-]*")


def _tokens(text: str) -> set[str]:
    """抽取用于相关性比对的内容 token：CJK 连续段 + 其二元组 + 字母数字段。"""
    toks: set[str] = set()
    for m in _CJK_RUN_RE.finditer(text):
        seg = m.group(0)
        toks.add(seg)
        for i in range(len(seg) - 1):
            toks.add(seg[i:i + 2])
    for m in _ALNUM_RE.finditer(text):
        t = m.group(0).lower()
        if len(t) >= 2:
            toks.add(t)
    return toks


def _related(a: str, b: str) -> bool:
    """两个文本是否共享内容 token（保守：任一共现即视为相关）。"""
    if not a or not b:
        return False
    return bool(_tokens(a) & _tokens(b))


def _fact_assertion_segments(article: str) -> list[str]:
    """拆分正文，返回含具体事实/数字断言的句子片段。"""
    sentences = re.split(r"[。！？!?\n]+", article)
    segments: list[str] = []
    for s in sentences:
        s = s.strip()
        if not s:
            continue
        if any(marker in s for marker in _FACT_MARKERS) or _NUMBER_ASSERTION_RE.search(s):
            segments.append(s)
    return segments


def _has_empty_hedge(article: str) -> bool:
    return any(marker in article for marker in _EMPTY_HEDGE_MARKERS)


def _has_conclusion(article: str) -> bool:
    return any(marker in article for marker in _CONCLUSION_MARKERS)


def _verified_sources(sources: dict) -> list[dict]:
    return [s for s in _as_list(sources.get("sources"))
            if isinstance(s, dict) and s.get("status") == "verified"]


def editorial_blockers(
    *,
    article: str,
    claims: dict | None = None,
    sources: dict | None = None,
    personal_materials_available: bool | None = None,
    disabled: Iterable[str] = (),
) -> list[str]:
    """返回审稿红线阻断项（中文，一句话一条）。无红线信号返回空列表。

    `disabled` 为需要关闭的开关名集合（反证用），缺省全部开启。
    """
    disabled_set = set(disabled or ())
    blockers: list[str] = []
    claims_data = _as_dict(claims)
    sources_data = _as_dict(sources)
    source_list = _as_list(sources_data.get("sources"))
    source_by_id = {
        s.get("id"): s
        for s in source_list
        if isinstance(s, dict) and s.get("id")
    }
    statuses = {s.get("status") for s in source_list if isinstance(s, dict)}
    fact_claims = [
        c for c in _as_list(claims_data.get("claims"))
        if isinstance(c, dict) and c.get("type") == _FACT_TYPE
    ]

    # 红线一：编造亲历/采访/场景。有第一人称事件/访谈标记，却没有用户提供的材料来源。
    if GUARD_FABRICATION not in disabled_set:
        if any(marker in article for marker in _FABRICATED_MARKERS):
            has_user_material = "user_provided" in statuses or personal_materials_available is True
            if not has_user_material:
                blockers.append(
                    "编造亲历/采访/场景：正文出现第一人称经历/采访/场景叙述，但没有本次任务用户明确提供的材料来源"
                )

    # 红线二：核心具体事实没有来源（或来源不支持正文说法）。
    if GUARD_UNSOURCED_FACT not in disabled_set:
        body_has_fact_signal = any(marker in article for marker in _FACT_MARKERS) or bool(_NUMBER_ASSERTION_RE.search(article))
        if body_has_fact_signal:
            verified = _verified_sources(sources_data)
            if not verified:
                blockers.append(
                    "核心具体事实无来源：正文出现数字/研究/数据/统计类断言，但来源账本没有 verified 来源"
                )
            else:
                # 每条事实断言必须至少被一条 verified 来源的 claim/title 支撑。
                uncovered = [
                    seg for seg in _fact_assertion_segments(article)
                    if not any(
                        _related(seg, (s.get("claim") or "") + (s.get("title") or ""))
                        for s in verified
                    )
                ]
                if uncovered:
                    blockers.append(
                        "核心具体事实无来源：正文断言与来源账本中任何 verified 来源都不相关，来源不支持正文说法"
                    )

    # 红线三：论点与证据脱节。fact 主张的 source_ids 必须解析到真实 verified 来源，
    # 且来源 claim 与主张文本相关；解析不到或无关来源即脱节。
    if GUARD_DISCONNECT not in disabled_set:
        for claim in fact_claims:
            text = claim.get("text") or ""
            source_ids = _as_list(claim.get("source_ids"))
            if not source_ids:
                blockers.append(
                    "论点与证据脱节：fact 类型主张缺少 source_ids（必须引用来源，否则删除或标为 unsupported）"
                )
                continue

            resolved: list[dict] = []
            missing: list[str] = []
            unverified: list[str] = []
            for sid in source_ids:
                entry = source_by_id.get(sid)
                if entry is None:
                    missing.append(str(sid))
                elif entry.get("status") != "verified":
                    unverified.append(str(sid))
                else:
                    resolved.append(entry)

            if missing:
                blockers.append(
                    "论点与证据脱节：fact 主张引用的来源编号 " + "、".join(missing) + " 在来源账本中无法解析"
                )
            if unverified:
                blockers.append(
                    "论点与证据脱节：fact 主张引用的来源 " + "、".join(unverified) + " 状态不是 verified"
                )
            if resolved and text and not any(
                _related(text, entry.get("claim") or "") for entry in resolved
            ):
                blockers.append(
                    "论点与证据脱节：fact 主张引用的来源 claim 不支撑该断言"
                )

    # 红线四：文章没有给目标读者明确的结论、行动或判断方法。
    if GUARD_NO_CONCLUSION not in disabled_set:
        if _has_empty_hedge(article) and not _has_conclusion(article):
            blockers.append(
                "文章没有给目标读者明确的结论、行动或判断方法"
            )

    # 去重（保持顺序）。
    return list(dict.fromkeys(blockers))
