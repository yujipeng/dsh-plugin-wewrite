"""编辑红线守卫（方案 A）：审稿时按 editorial-quality.md 的「不可交付」红线做确定性拦截。

- 不替代模型编辑判断，只做「伪 pass 拦截」：正文/sources/claims 出现可判定的红线信号时，
  无论模型自报的 decision/blocks 如何，一律降级为 revise 并返回阻断项。
- 判定全部基于产物文本与来源账本（sources.yaml）、主张清单（claims.yaml）与任务书
  （brief.yaml 的 personal_materials.available），不依赖模型自评分数。
- 保守：只认明确信号，避免把普通叙述误判为「具体事实」或「编造亲历」。
"""

from __future__ import annotations

from typing import Any

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

# 论点与证据脱节：fact 主张必须有来源（对应 article-brief.md「fact 必须有能直接支持它的来源」）。
_FACT_TYPE = "fact"


def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _source_statuses(sources: dict) -> set[str]:
    return {item.get("status") for item in _as_list(sources.get("sources")) if isinstance(item, dict)}


def editorial_blockers(
    *,
    article: str,
    claims: dict | None = None,
    sources: dict | None = None,
    personal_materials_available: bool | None = None,
) -> list[str]:
    """返回审稿红线阻断项（中文，一句话一条）。无红线信号返回空列表。"""
    blockers: list[str] = []
    claims_data = _as_dict(claims)
    sources_data = _as_dict(sources)
    statuses = _source_statuses(sources_data)

    # 红线一：编造亲历/采访/场景。有第一人称事件/访谈标记，却没有用户提供的材料来源。
    if any(marker in article for marker in _FABRICATED_MARKERS):
        has_user_material = "user_provided" in statuses or personal_materials_available is True
        if not has_user_material:
            blockers.append(
                "编造亲历/采访/场景：正文出现第一人称经历/采访/场景叙述，但没有本次任务用户明确提供的材料来源"
            )

    # 红线二：核心具体事实没有来源。有数字/研究/数据/统计类断言，却没有 verified 来源。
    if any(marker in article for marker in _FACT_MARKERS):
        if "verified" not in statuses:
            blockers.append(
                "核心具体事实无来源：正文出现数字/研究/数据/统计类断言，但来源账本没有 verified 来源"
            )

    # 红线三：论点与证据脱节。fact 主张必须引用来源；缺少来源编号的 fact 主张视为脱节。
    for item in _as_list(claims_data.get("claims")):
        if not isinstance(item, dict):
            continue
        if item.get("type") != _FACT_TYPE:
            continue
        source_ids = _as_list(item.get("source_ids"))
        if not source_ids:
            blockers.append(
                "论点与证据脱节：fact 类型主张缺少 source_ids（必须引用来源，否则删除或标为 unsupported）"
            )

    # 去重（保持顺序）。
    return list(dict.fromkeys(blockers))
