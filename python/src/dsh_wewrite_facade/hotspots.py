"""热榜（设计 §6）：调上游 fetch_weibo/fetch_toutiao/fetch_baidu/deduplicate 并复刻排序聚合。

不调解析 sys.argv 的 main()；来源失败部分返回，不伪造新鲜数据。
hotNormalized 复用上游算法：每 source 按原始 hot 降序，0-based rank 为 r 时
round(100*(n-r)/n,1)，各源归一化后才全局降序并按 limit 截断。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any

from .counters import get_counters


def _fetch_source(name: str, fn) -> tuple[str, list[dict]]:
    try:
        items = fn()
    except Exception:
        items = []
    if not isinstance(items, list):
        items = []
    return name, items


def fetch_hotspots(limit: int) -> dict[str, Any]:
    from wewrite.commands.fetch_hotspots import (
        deduplicate,
        fetch_baidu,
        fetch_toutiao,
        fetch_weibo,
    )

    fetchers = {"weibo": fetch_weibo, "toutiao": fetch_toutiao, "baidu": fetch_baidu}
    all_items: list[dict] = []
    sources_ok: list[str] = []
    sources_failed: list[str] = []

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(_fetch_source, name, fn): name for name, fn in fetchers.items()}
        for future in as_completed(futures):
            name, items = future.result()
            if items:
                sources_ok.append(name)
                all_items.extend(items)
            else:
                sources_failed.append(name)

    all_items = deduplicate(all_items)

    by_source: dict[str, list[dict]] = {}
    for item in all_items:
        by_source.setdefault(item.get("source", "未知"), []).append(item)

    for source, items in by_source.items():
        items.sort(key=lambda x: int(x.get("hot", 0) or 0), reverse=True)
        n = len(items)
        for rank, item in enumerate(items):
            item["hot_normalized"] = round(100 * (n - rank) / n, 1) if n > 0 else 0

    all_items.sort(key=lambda x: x.get("hot_normalized", 0), reverse=True)
    all_items = all_items[:limit]

    # 输出契约字段：title/url/source/hotNormalized。
    items_out = [
        {
            "title": str(item.get("title", "")),
            "url": str(item.get("url", "")),
            "source": str(item.get("source", "")),
            "hotNormalized": float(item.get("hot_normalized", 0) or 0),
        }
        for item in all_items
    ]

    return {
        "timestamp": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "items": items_out,
        "sourcesFailed": sources_failed,
    }
