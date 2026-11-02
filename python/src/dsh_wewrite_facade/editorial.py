"""编辑红线守卫（方案 A 返修 3）：审稿时按 editorial-quality.md 的「不可交付」红线做确定性拦截。

判定方向（按项目编排裁决，不再依赖宽泛关键词表）：
- 来源支持 = 断言要素被覆盖：数字/量级要素必须逐一被来源覆盖（同一数值 + 主体词一致），
  非数字事实断言要求来源覆盖断言主干（主体 + 谓述核心词）；数值不一致 / 主体不一致 /
  来源无该数值，一律判「来源不支持」，fail-closed。
- 第四条红线 = 明确结论必须存在：结尾区必须出现真正的结论句（结论动词 + 具体主张），
  空泛收尾命中时不再被普通正向词豁免；无法识别明确结论时判 revise。
- blocker 写清「哪条断言、缺哪个要素、哪条来源不覆盖」，便于复验核对。

不替代模型编辑判断，只做「伪 pass 拦截」。判定基于产物文本与来源账本（sources.yaml）、
主张清单（claims.yaml）与任务书（brief.yaml 的 personal_materials.available），不依赖模型自评分数。
"""

from __future__ import annotations

import re
from typing import Any, Iterable

# 四条红线的开关名（保留给反证/调试，缺省全部开启）。
GUARD_FABRICATION = "fabrication"
GUARD_UNSOURCED_FACT = "unsourced_fact"
GUARD_DISCONNECT = "disconnect"
GUARD_NO_CONCLUSION = "no_conclusion"
GUARD_NAMES = (GUARD_FABRICATION, GUARD_UNSOURCED_FACT, GUARD_DISCONNECT, GUARD_NO_CONCLUSION)

# ── 红线一：编造亲历/采访/场景 ──────────────────────────────────────────────
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

# ── 红线二/三：核心具体事实与来源对应 ──────────────────────────────────────
# 具体事实的显式信号（数字/百分比/引用/研究/数据/统计/调查/报道）。
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
)

# 数字 + 量级单位（要求逐一被来源覆盖）。
_NUMBER_UNIT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(%|％|倍|成|个百分点)")
_ALNUM_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._%\-]*")

# 常见实物名词（用于从数字后提取主体词；命中即返回，未命中回退为相邻 CJK 两字）。
_SUBJECT_NOUNS = (
    "用户", "读者", "消费者", "受访者", "人群", "企业", "公司", "学生", "家长",
    "员工", "网民", "客户", "商户", "开发者", "教师", "医生", "患者", "车主",
    "购房者", "求职者", "投资人", "商家",
)

# 谓述比对时剔除的虚词/功能字。
_FUNCTION_CHARS = set("的了是在对有与和之地得吗呢么又很也就还着过把被让等个些这那们及而或可会")

# 空泛收尾信号（红线四的 fail 信号）。
_HEDGE_PHRASES = (
    "莫衷一是", "无法给出答案", "无法给出", "留待以后", "以后再", "以后再谈",
    "争议不断", "孰是孰非", "有很多看法", "尚无定论", "暂无定论", "没有定论",
    "见仁见智", "众说纷纭", "众说纷", "看法不一", "各有各的", "不好说",
    "难以判断", "自行判断", "留待", "暂且无法", "尚需观察", "待观察",
    "未有定论", "未有结论",
)

# 明确结论信号（结论动词 + 具体主张）；不再包含「可以/判断/方法/需要/首先/其次」等宽泛词。
_STRONG_CONCLUSION_MARKERS = (
    "结论是", "我的结论", "本文结论", "得出结论", "综上", "总之", "因此", "所以",
    "建议", "应该", "取决于", "关键在于", "核心是", "最重要的是", "我认为",
    "我的判断", "判断是", "答案是", "推荐", "应当",
)

_FACT_TYPE = "fact"


# ── 工具函数 ────────────────────────────────────────────────────────────────

def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _num_key(num: str):
    """数值归一化：同一数值（90 / 90.0）视为相等；文本回退按字符串。"""
    try:
        return float(num)
    except (TypeError, ValueError):
        return num


def _subject_after(text: str, pos: int) -> str:
    """从数字+单位之后提取主体词：优先命中常见实物名词，否则回退相邻 CJK 两字。"""
    tail = text[pos:pos + 24]
    for noun in sorted(_SUBJECT_NOUNS, key=len, reverse=True):
        if noun in tail:
            return noun
    m = re.match(r"[\s的之其中受访者人群]*([\u4e00-\u9fff]{2})", tail)
    return m.group(1) if m else ""


def _number_elements(text: str) -> list[dict[str, str]]:
    """抽取 (num, unit, subject) 三元组。"""
    elems: list[dict[str, str]] = []
    for m in _NUMBER_UNIT_RE.finditer(text):
        elems.append({
            "num": m.group(1),
            "unit": m.group(2),
            "subject": _subject_after(text, m.end()),
        })
    return elems


def _content_bigrams(text: str) -> set[str]:
    """抽取用于谓述比对的内容 bigram（剔除虚词/功能字）+ 字母数字 token。"""
    cleaned = re.sub(r"[\s\W_]+", "", text, flags=re.UNICODE)
    bigrams: set[str] = set()
    cjk = re.findall(r"[\u4e00-\u9fff]", cleaned)
    for i in range(len(cjk) - 1):
        a, b = cjk[i], cjk[i + 1]
        if a in _FUNCTION_CHARS or b in _FUNCTION_CHARS:
            continue
        bigrams.add(a + b)
    for t in _ALNUM_RE.findall(text):
        t = t.lower()
        if len(t) >= 2:
            bigrams.add(t)
    return bigrams


def _assertion_subject(text: str) -> str:
    for noun in sorted(_SUBJECT_NOUNS, key=len, reverse=True):
        if noun in text:
            return noun
    return ""


def _fact_assertion_segments(article: str) -> list[str]:
    """拆分正文，返回含具体事实/数字断言的句子片段。"""
    sentences = re.split(r"[。！？!?\n]+", article)
    segments: list[str] = []
    for s in sentences:
        s = s.strip()
        if not s:
            continue
        if any(marker in s for marker in _FACT_MARKERS) or _NUMBER_UNIT_RE.search(s):
            segments.append(s)
    return segments


def _assertion_supported(assertion: str, source_text: str) -> tuple[bool, str | None]:
    """判断一条事实断言是否被单个来源支持。返回 (supported, reason)。

    数字/量级要素逐一核对：同一数值 + 同一单位 + 主体词一致（主体需出现在来源文本）。
    非数字断言：主体必须出现在来源，且谓述内容 bigram 至少共享 2 个。
    """
    elems = _number_elements(assertion)
    if elems:
        source_elems = _number_elements(source_text)
        for elem in elems:
            covered = any(
                _num_key(se["num"]) == _num_key(elem["num"]) and se["unit"] == elem["unit"]
                and (not elem["subject"] or elem["subject"] in source_text)
                for se in source_elems
            )
            if not covered:
                subject = elem["subject"] or "（未识别主体）"
                return False, f"数值 {elem['num']}{elem['unit']}（主体「{subject}」）未被来源覆盖或数值不一致"
        return True, None

    # 非数字事实断言：主干覆盖（主体 + 谓述核心词）。
    subject = _assertion_subject(assertion)
    if subject and subject not in source_text:
        return False, f"断言主体「{subject}」未出现在来源中"
    shared = _content_bigrams(assertion) & _content_bigrams(source_text)
    if len(shared) < 2:
        return False, f"来源与断言仅共享 {len(shared)} 个内容词，谓述不覆盖"
    return True, None


def _verified_sources(sources: dict) -> list[dict]:
    return [s for s in _as_list(sources.get("sources"))
            if isinstance(s, dict) and s.get("status") == "verified"]


def _source_text(entry: dict) -> str:
    return (entry.get("claim") or "") + " " + (entry.get("title") or "")


def _ending(text: str) -> str:
    tail = text.strip()
    if len(tail) > 200:
        tail = tail[-200:]
    return tail


def _has_hedge(text: str) -> bool:
    return any(h in text for h in _HEDGE_PHRASES)


def _has_conclusion(text: str) -> bool:
    return any(m in text for m in _STRONG_CONCLUSION_MARKERS)


# ── 主判定 ──────────────────────────────────────────────────────────────────

def editorial_blockers(
    *,
    article: str,
    claims: dict | None = None,
    sources: dict | None = None,
    personal_materials_available: bool | None = None,
    disabled: Iterable[str] = (),
) -> list[str]:
    """返回审稿红线阻断项（中文，一句话一条）。无红线信号返回空列表。"""
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

    # 红线一：编造亲历/采访/场景。
    if GUARD_FABRICATION not in disabled_set:
        if any(marker in article for marker in _FABRICATED_MARKERS):
            has_user_material = "user_provided" in statuses or personal_materials_available is True
            if not has_user_material:
                blockers.append(
                    "编造亲历/采访/场景：正文出现第一人称经历/采访/场景叙述，但没有本次任务用户明确提供的材料来源"
                )

    # 红线二：核心具体事实没有来源（或来源不支持正文说法）。
    if GUARD_UNSOURCED_FACT not in disabled_set:
        segments = _fact_assertion_segments(article)
        if segments:
            verified = _verified_sources(sources_data)
            if not verified:
                blockers.append(
                    "核心具体事实无来源：正文出现数字/研究/数据类断言，但来源账本没有 verified 来源"
                )
            else:
                for seg in segments:
                    supported = any(
                        _assertion_supported(seg, _source_text(s))[0]
                        for s in verified
                    )
                    if not supported:
                        blockers.append(
                            f"核心具体事实无来源或来源不支持：断言「{seg}」缺匹配的 verified 来源"
                        )

    # 红线三：论点与证据脱节。fact 主张的 source_ids 必须解析到真实 verified 来源，
    # 且来源 claim 必须支撑该主张断言。
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
                _assertion_supported(text, _source_text(entry))[0]
                for entry in resolved
            ):
                blockers.append(
                    "论点与证据脱节：fact 主张引用的来源 claim 不支撑该断言（缺要素或数值不一致）"
                )

    # 红线四：文章必须给目标读者明确的结论、行动或判断方法（fail-closed）。
    if GUARD_NO_CONCLUSION not in disabled_set:
        ending = _ending(article)
        hedge = _has_hedge(ending)
        conclusion = _has_conclusion(ending)
        if not conclusion:
            if hedge:
                blockers.append("文章没有给目标读者明确的结论、行动或判断方法")
            else:
                blockers.append("未能识别明确结论，请补结论/行动/判断方法")

    return list(dict.fromkeys(blockers))
