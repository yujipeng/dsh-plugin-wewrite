"""rewrite 账号级多平台改写质量门（方案 B 批二新增）。

- ``rewrite.score``：复用上游 ``wewrite.commands.humanness_score.score_article``，对一份改写
  产物做机械语言风险打分，返回 ``quality_score``（0–100，越高越好，≥60 为「建议通过」）。
  纯本地文本分析，不涉网络、不修改任何文件。
- ``rewrite.similarity``：复用上游 ``wewrite.commands.similarity_check`` 的字符 3-gram
  Jaccard，计算源稿与改写产物之间的 ``max_similarity``（0–1，≤0.6 为「建议通过」）。
  纯本地文本分析，不修改源稿、不落盘。

两者都只接收明文文本（经契约大小上限约束），不接收文件路径、不读任意文件——改写产物
由 Agent 经 ``wewrite_artifact_write`` 落盘到 run，源稿经 ``wewrite_artifact_get`` 读取，
相似度/评分由本模块对明文计算。这样既不引入 Bash/文件读取工具，也守住「不修改源稿」。
"""

from __future__ import annotations

from typing import Any


def rewrite_score(payload: dict[str, Any]) -> dict[str, Any]:
    """rewrite.score：返回上游质量分（quality_score 0–100）。"""
    from wewrite.commands.humanness_score import score_article

    text = payload["text"]
    result = score_article(text)
    return {
        "quality_score": result.get("quality_score"),
        "composite_score": result.get("composite_score"),
        "char_count": result.get("char_count"),
        "param_scores": result.get("param_scores"),
    }


def rewrite_similarity(payload: dict[str, Any]) -> dict[str, Any]:
    """rewrite.similarity：源稿与每个改写版本两两的最大相似度（0–1）。"""
    from wewrite.commands.similarity_check import max_pairwise

    source = payload["source"]
    variants = payload["variants"]
    if not isinstance(variants, list):
        raise ValueError("variants 不合法")

    per_variant: list[dict[str, Any]] = []
    pairwise_max = 0.0
    for variant in variants:
        if not isinstance(variant, str):
            continue
        sim = max_pairwise([source, variant])
        pairwise_max = max(pairwise_max, sim)
        per_variant.append({"similarity": round(sim, 4)})

    # 源稿与所有改写版本一起做两两最大（含不同平台版本之间），对齐上游 max_pairwise 语义。
    if len(variants) >= 2:
        all_texts = [source, *[v for v in variants if isinstance(v, str)]]
        pairwise_max = max(pairwise_max, max_pairwise(all_texts))

    return {
        "max_similarity": round(pairwise_max, 4),
        "param": "similarity_threshold",
        "variants": per_variant,
    }
