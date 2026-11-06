"""stats 账号级数据复盘（方案 B 批二新增）。

- ``stats.fetch``：在**服务端**读取 config.yaml / 环境变量里的微信凭证（不经 RPC/工具/
  日志/前端回显），拉取公众号文章阅读/分享/点赞数据并回填 history.yaml 的 stats 字段。
  成功分支返回逐篇脱敏指标（``articles``），供 Agent 端做最好/最差表现分析；**无凭证时
  返回明确的降级结果**（``degraded=true``，只能基于 history.yaml 已有记录做定性分析），
  且不产生任何微信 API 调用（``wechatApiCalls`` 恒为 0）。
- ``stats.backfill``：把已获取的 stats 数据按 media_id（精确）/title（回退）匹配回填
  history.yaml。账号级写操作，仅 owner 可改（携带绑定上下文一律 FORBIDDEN）。
- history.yaml 位于 ``{home}`` 根；读写走 facade 安全原语（O_NOFOLLOW + home 全局锁 +
  同目录临时文件 + replace 原子写），**不**经上游 ``save_history`` 的裸 Path 写。
- 回填的读-改-写全程在同一把 ``home_lock`` 内（``home_lock`` 不可重入，锁内只用
  「调用方已持锁」的原子写原语，不二次取锁），杜绝并发丢更新。
- 每日微信摘要响应逐日校验（HTTP 成功 / dict / 无 errcode / list 为数组），任一失败
  即整批业务失败且不写 history.yaml（半份数据比没有更糟）。
- 复用上游 ``wewrite.history`` 的形状规范（``version:1`` + ``articles``），与选题模块
  wewrite-topic 读取的 history.yaml 结构一致。
- 时间经 ``_now`` 注入点（测试可 monkeypatch，不引入时间炸弹）。
"""

from __future__ import annotations

import os
import stat
from datetime import datetime, timedelta
from typing import Any

import yaml

from . import paths, storage
from .errors import FacadeError, forbidden, invalid_input

HISTORY_FILENAME = "history.yaml"
HISTORY_VERSION = 1

# stats 数据拉取允许的微信端点（与 publish.py 的发布白名单分离：这里只放数据复盘端点）。
_STATS_TOKEN_ENDPOINT = "cgi-bin/token"
_STATS_SUMMARY_ENDPOINT = "datacube/getarticlesummary"
_STATS_TOTAL_ENDPOINT = "datacube/getarticletotal"
_STATS_ENDPOINTS = (_STATS_TOKEN_ENDPOINT, _STATS_SUMMARY_ENDPOINT, _STATS_TOTAL_ENDPOINT)

_API_TIMEOUT = 30
_HISTORY_MAX_BYTES = 4 * 1024 * 1024


def _now() -> datetime:
    return datetime.now()


def _open_root(*, create: bool) -> int:
    root = paths.home()
    if create:
        os.makedirs(root, exist_ok=True)
    return paths._open_root()


def _read_home_file_bytes(name: str) -> bytes | None:
    """安全读 home 根下固定文件；不存在（ENOENT）返回 None。

    存在但不可读 / 是 symlink（ELOOP，O_NOFOLLOW 命中）/ 非普通文件一律 fail-closed
    （FORBIDDEN），不静默读、更不静默当空——防止历史文件被预置为逃逸 symlink 时
    回填路径误判为「无历史」而覆盖。
    """
    try:
        root_fd = _open_root(create=False)
    except Exception:
        return None
    try:
        try:
            fd = os.open(name, os.O_RDONLY | paths._O_NOFOLLOW | paths._O_BINARY, dir_fd=root_fd)
        except FileNotFoundError:
            return None
        except OSError as exc:
            if getattr(exc, "errno", None) == 2:  # ENOENT 双保险。
                return None
            raise forbidden(f"{name} 不是普通文件，拒绝读取（{paths._os_reason(exc)}）") from exc
        with os.fdopen(fd, "rb") as f:
            st = os.fstat(f.fileno())
            if not stat.S_ISREG(st.st_mode) or st.st_nlink < 1:
                raise forbidden(f"{name} 不是普通文件")
            return f.read()
    finally:
        os.close(root_fd)


def _write_home_file_atomic(name: str, data: bytes) -> str:
    """home 根下原子写（同目录临时文件 + replace）。调用方须已持 home 锁。返回字节哈希。"""
    root_fd = _open_root(create=True)
    try:
        storage._write_file_atomically(root_fd, name, data)
    finally:
        os.close(root_fd)
    return storage.sha256_hex(data)


def _normalize_history(data: Any) -> dict[str, Any]:
    """与上游 wewrite.history.normalize_history 同构：任何形态归一为 {version, articles}。"""
    if isinstance(data, list):
        return {"version": HISTORY_VERSION, "articles": data}
    if isinstance(data, dict):
        articles = data.get("articles", [])
        if not isinstance(articles, list):
            articles = []
        return {**data, "version": data.get("version", HISTORY_VERSION), "articles": articles}
    return {"version": HISTORY_VERSION, "articles": []}


def read_history() -> dict[str, Any]:
    """安全读 history.yaml 并归一化；不存在返回空结构。读面不建目录/锁文件。"""
    raw = _read_home_file_bytes(HISTORY_FILENAME)
    if raw is None:
        return _normalize_history(None)
    try:
        data = yaml.safe_load(raw.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError):
        return _normalize_history(None)
    return _normalize_history(data)


def _write_history_locked(data: dict[str, Any]) -> str:
    """**调用方已持 home 锁**：原子写 history.yaml 并返回新字节哈希。

    不在此处再取 home 锁（``home_lock`` 不可重入，嵌套会永久阻塞）。
    """
    canonical = _normalize_history(data)
    text = yaml.safe_dump(canonical, allow_unicode=True, sort_keys=False)
    raw = text.encode("utf-8")
    if len(raw) > _HISTORY_MAX_BYTES:
        raise invalid_input("history.yaml 超过 4 MiB 上限")
    return _write_home_file_atomic(HISTORY_FILENAME, raw)


def wechat_credentials() -> tuple[str, str] | None:
    """只在服务端读取微信凭证（config.yaml + WECHAT_APPID/WECHAT_SECRET）。缺凭证返回 None。

    绝不把 appid/secret 写进返回值以外的任何日志/响应；调用方只使用、不回显。
    """
    try:
        from wewrite.toolkit.config import get_wechat_credentials
        return get_wechat_credentials()
    except ValueError:
        return None
    except Exception:
        return None


def has_wechat_credentials() -> bool:
    return wechat_credentials() is not None


def _assert_stats_endpoint(endpoint: str) -> None:
    if endpoint not in _STATS_ENDPOINTS:
        raise forbidden("微信数据端点不在白名单内")


def _fetch_real_stats(appid: str, secret: str, days: int) -> list[dict[str, Any]]:
    """真实拉取最近 days 天的文章摘要数据。每个微信调用前过白名单并计入 wechatApiCalls。

    每日摘要响应**逐日严格校验**：HTTP 调用成功、响应是 dict、无 errcode（或 errcode==0）、
    ``list`` 是数组。任一条不满足即抛业务失败（整批失败，不写历史），绝不静默跳过某日
    再以 ``degraded=false`` 冒充成功。
    """
    import requests

    from .counters import get_counters

    _assert_stats_endpoint(_STATS_TOKEN_ENDPOINT)
    get_counters().inc_wechat()
    try:
        resp = requests.get(
            "https://api.weixin.qq.com/cgi-bin/token",
            params={"grant_type": "client_credential", "appid": appid, "secret": secret},
            timeout=_API_TIMEOUT,
        )
        token_body = resp.json()
    except Exception as exc:
        raise FacadeError("CAPABILITY_UNAVAILABLE", "微信数据接口令牌获取失败", retryable=True) from exc
    if not isinstance(token_body, dict) or "access_token" not in token_body:
        raise FacadeError("CAPABILITY_UNAVAILABLE", "微信数据接口令牌获取失败", retryable=False)
    token = token_body["access_token"]

    all_stats: list[dict[str, Any]] = []
    for i in range(days):
        date = (_now() - timedelta(days=i + 1)).strftime("%Y-%m-%d")
        _assert_stats_endpoint(_STATS_SUMMARY_ENDPOINT)
        get_counters().inc_wechat()
        try:
            resp = requests.post(
                "https://api.weixin.qq.com/datacube/getarticlesummary",
                params={"access_token": token},
                json={"begin_date": date, "end_date": date},
                timeout=_API_TIMEOUT,
            )
            body = resp.json()
        except Exception as exc:
            raise FacadeError("CAPABILITY_UNAVAILABLE", f"{date} 微信数据接口调用失败", retryable=True) from exc
        # 逐日校验：①是 dict ②无 errcode（或 errcode==0）③list 是数组。
        if not isinstance(body, dict):
            raise FacadeError("CAPABILITY_UNAVAILABLE", f"{date} 微信数据接口响应不合法", retryable=True)
        errcode = body.get("errcode")
        if errcode is not None and errcode != 0:
            raise FacadeError("CAPABILITY_UNAVAILABLE", f"{date} 微信数据接口返回错误（errcode={errcode}）", retryable=True)
        if not isinstance(body.get("list"), list):
            raise FacadeError("CAPABILITY_UNAVAILABLE", f"{date} 微信数据接口响应不合法", retryable=True)
        all_stats.extend(body["list"])
    return all_stats


def _stat_to_stored(stat: dict[str, Any]) -> dict[str, Any]:
    """把微信 datacube 数据点归一为 history.yaml 的 stats 字段（与上游 fetch_stats.py 一致）。"""
    read_count = stat.get("int_page_read_count", 0)
    if not isinstance(read_count, (int, float)):
        read_count = 0
    share_count = stat.get("share_count", 0)
    if not isinstance(share_count, (int, float)):
        share_count = 0
    like_count = (stat.get("old_like_count", 0) or 0) + (stat.get("like_count", 0) or 0)
    target_user = stat.get("target_user", 1) or 1
    try:
        read_rate = round(read_count / max(target_user, 1) * 100, 1)
    except (TypeError, ZeroDivisionError):
        read_rate = 0.0
    return {
        "read_count": int(read_count),
        "share_count": int(share_count),
        "like_count": int(like_count),
        "read_rate": read_rate,
    }


def _article_metric(stat: dict[str, Any], matched: bool) -> dict[str, Any]:
    """逐篇脱敏指标：只含 media_id/title/stats/matched，绝不回显任何凭据。"""
    return {
        "media_id": stat.get("media_id"),
        "title": stat.get("title"),
        "stats": _stat_to_stored(stat),
        "matched": matched,
    }


def backfill_history(stats_list: list[dict[str, Any]]) -> tuple[int, list[dict[str, Any]]]:
    """按 media_id（精确）/title（回退）把 stats 回填进 history.yaml。

    读 → 匹配 → 修改 → 原子替换**全程在同一把 home 锁内**，杜绝并发丢更新。
    返回 ``(updated, articles)``：``articles`` 为逐篇脱敏指标（含 matched 标识），
    供 ``stats.fetch`` 成功分支回传与 Agent 端分析。
    """
    with storage.home_lock(create=True):
        history = read_history()
        articles = history.get("articles", [])
        if not isinstance(articles, list):
            articles = []

        media_id_to_idx: dict[str, int] = {}
        title_to_idx: dict[str, int] = {}
        for i, article in enumerate(articles):
            if not isinstance(article, dict):
                continue
            mid = article.get("media_id", "")
            if isinstance(mid, str) and mid:
                media_id_to_idx[mid] = i
            title = article.get("title", "")
            if isinstance(title, str) and title:
                title_to_idx[title] = i

        updated = 0
        out_articles: list[dict[str, Any]] = []
        for stat in stats_list:
            if not isinstance(stat, dict):
                continue
            stat_media_id = stat.get("media_id", "")
            stat_title = stat.get("title", "")
            idx = None
            matched = False
            if isinstance(stat_media_id, str) and stat_media_id and stat_media_id in media_id_to_idx:
                idx = media_id_to_idx[stat_media_id]
                matched = True
            elif isinstance(stat_title, str) and stat_title and stat_title in title_to_idx:
                idx = title_to_idx[stat_title]
                matched = True
            if idx is not None:
                articles[idx]["stats"] = _stat_to_stored(stat)
                updated += 1
            out_articles.append(_article_metric(stat, matched))

        if updated > 0:
            history["articles"] = articles
            _write_history_locked(history)
        return updated, out_articles


def _degraded_result(days: int) -> dict[str, Any]:
    history = read_history()
    articles = history.get("articles", []) if isinstance(history, dict) else []
    if not isinstance(articles, list):
        articles = []
    items: list[dict[str, Any]] = []
    with_stats = 0
    for a in articles:
        if not isinstance(a, dict):
            continue
        item: dict[str, Any] = {
            "title": a.get("title"),
            "media_id": a.get("media_id"),
            "run_id": a.get("run_id"),
        }
        stats = a.get("stats")
        if isinstance(stats, dict):
            item["stats"] = stats
            with_stats += 1
        items.append(item)
    return {
        "degraded": True,
        "reason": "数据复盘需要配置公众号 API（config.yaml），当前只能基于 history.yaml 已有记录做定性分析",
        "days": days,
        "fetched": 0,
        "backfilled": 0,
        "wechatApiCalls": 0,
        "history": {"articleCount": len(articles), "withStats": with_stats, "items": items},
    }


def stats_fetch(payload: dict[str, Any]) -> dict[str, Any]:
    """stats.fetch：有凭证真实拉取并回填；无凭证返回降级结果且零微信调用。

    成功分支返回逐篇脱敏指标 ``articles``（media_id/title/stats/matched），不回显任何凭据。
    """
    days = payload["days"]
    creds = wechat_credentials()
    if creds is None:
        return _degraded_result(days)

    appid, secret = creds
    try:
        fetched = _fetch_real_stats(appid, secret, days)
    except FacadeError:
        raise
    except Exception:
        raise FacadeError("CAPABILITY_UNAVAILABLE", "微信数据接口不可用，请稍后重试", retryable=True)

    backfilled, articles = backfill_history(fetched)
    return {
        "degraded": False,
        "days": days,
        "fetched": len(fetched),
        "backfilled": backfilled,
        "mediaIds": [s.get("media_id") for s in fetched if isinstance(s, dict) and s.get("media_id")],
        "articles": articles,
        "historyVersion": storage.sha256_hex(
            yaml.safe_dump(read_history(), allow_unicode=True, sort_keys=False).encode("utf-8")
        ),
    }


def stats_backfill(payload: dict[str, Any]) -> dict[str, Any]:
    """stats.backfill：账号级写操作，仅 owner 可改（Agent 写路径 FORBIDDEN）。"""
    if payload.get("context"):
        raise forbidden("数据回填为账号级配置，仅 owner 可改")
    entries = payload["entries"]
    backfilled, _articles = backfill_history(entries)
    return {
        "backfilled": backfilled,
        "historyVersion": storage.sha256_hex(
            yaml.safe_dump(read_history(), allow_unicode=True, sort_keys=False).encode("utf-8")
        ),
    }
