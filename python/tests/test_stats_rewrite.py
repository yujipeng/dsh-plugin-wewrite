"""方案 B 批二：stats.fetch/backfill + rewrite.score/similarity 正向与负向。

覆盖验收标准：
- 无凭证时 stats.fetch 返回「只能定性分析」的降级结果，且不产生任何微信 API 调用
  （wechatApiCalls === 0）。
- stats.backfill 按 media_id（精确）/title（回退）回填 history.yaml；账号级写操作
  携带 context 一律 FORBIDDEN。
- rewrite.score / rewrite.similarity 纯本地计算，不修改源稿、不写任何文件。
- 路径安全：history.yaml 被预置为 symlink 时写入必须拒绝（fail-closed）。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import yaml

from conftest import PYTHON_SRC, VENV_PY, call_facade, facade_env, make_home


def _seed_history(home: Path, articles: list[dict] | None = None) -> None:
    """直接在 home 根写一份 history.yaml（绕过 facade 构造夹具，含 media_id/title）。"""
    if articles is None:
        articles = [
            {"title": "第一篇", "media_id": "media-aaa", "run_id": "20261103-000000-aaaaaa"},
            {"title": "第二篇", "run_id": "20261103-000000-bbbbbb"},
        ]
    data = {"version": 1, "articles": articles}
    (home / "history.yaml").write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")


def _ctx():
    return {"runId": "20261103-000000-abcdef", "workflowId": "wf-a", "generation": 1}


def test_stats_fetch_degraded_without_credentials(tmp_path):
    """无凭证：返回降级结果、reason 提示只能定性分析、wechatApiCalls===0、无网络调用。"""
    home = make_home(tmp_path)
    _seed_history(home)
    # 确保环境里没有微信凭证（清空可能残留的环境变量）。
    env_keys = ("WECHAT_APPID", "WECHAT_SECRET")
    saved = {k: os.environ.get(k) for k in env_keys}
    for k in env_keys:
        os.environ.pop(k, None)
    try:
        resp = call_facade(str(home), "stats.fetch", {"days": 7})
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v
    assert resp["ok"] is True, resp
    data = resp["data"]
    assert data["degraded"] is True
    assert "定性分析" in data["reason"]
    assert data["fetched"] == 0
    assert data["backfilled"] == 0
    assert data["wechatApiCalls"] == 0
    assert data["history"]["articleCount"] == 2
    # 降级只读：history.yaml 未被改写（字节不变）。
    before = (home / "history.yaml").read_bytes()
    after = (home / "history.yaml").read_bytes()
    assert before == after


def test_stats_fetch_with_fake_credentials_fails_closed(tmp_path):
    """假凭证（非空但无效）：不得伪造成功，也不得静默降级——fail-closed 返回明确错误。"""
    home = make_home(tmp_path)
    _seed_history(home)
    os.environ["WECHAT_APPID"] = "fake-appid"
    os.environ["WECHAT_SECRET"] = "fake-secret"
    try:
        resp = call_facade(str(home), "stats.fetch", {"days": 1})
    finally:
        os.environ.pop("WECHAT_APPID", None)
        os.environ.pop("WECHAT_SECRET", None)
    # 有凭证则必须走真实路径；离线/无效凭证 → 业务失败（不是假成功、不是静默降级）。
    assert resp["ok"] is False, resp
    assert resp["error"]["code"] in ("CAPABILITY_UNAVAILABLE", "PUBLISH_UNKNOWN", "TIMEOUT"), resp
    # 任何失败路径都不得改写 history.yaml。
    on_disk = yaml.safe_load((home / "history.yaml").read_text(encoding="utf-8"))
    assert all("stats" not in a for a in on_disk["articles"])


def test_stats_backfill_roundtrip(tmp_path):
    home = make_home(tmp_path)
    _seed_history(home)
    entries = [
        {"media_id": "media-aaa", "int_page_read_count": 100, "share_count": 5,
         "like_count": 3, "old_like_count": 1, "target_user": 1000},
        {"title": "第二篇", "int_page_read_count": 50, "share_count": 2,
         "like_count": 1, "old_like_count": 0, "target_user": 500},
    ]
    resp = call_facade(str(home), "stats.backfill", {"entries": entries})
    assert resp["ok"] is True, resp
    assert resp["data"]["backfilled"] == 2

    on_disk = yaml.safe_load((home / "history.yaml").read_text(encoding="utf-8"))
    by_media = {a["media_id"]: a for a in on_disk["articles"] if "media_id" in a}
    by_title = {a["title"]: a for a in on_disk["articles"]}
    assert by_media["media-aaa"]["stats"]["read_count"] == 100
    assert by_media["media-aaa"]["stats"]["like_count"] == 4  # 3 + old_like 1
    assert by_media["media-aaa"]["stats"]["read_rate"] == 10.0  # 100/1000*100
    assert by_title["第二篇"]["stats"]["read_count"] == 50


def test_stats_backfill_forbidden_with_agent_context(tmp_path):
    home = make_home(tmp_path)
    _seed_history(home)
    resp = call_facade(str(home), "stats.backfill", {
        "entries": [{"media_id": "media-aaa", "int_page_read_count": 1}],
        "context": _ctx(),
    })
    assert resp["ok"] is False, resp
    assert resp["error"]["code"] == "FORBIDDEN"
    # 未被改写。
    data = yaml.safe_load((home / "history.yaml").read_text(encoding="utf-8"))
    assert "stats" not in data["articles"][0]


def test_stats_backfill_rejects_history_symlink_escape(tmp_path):
    home = make_home(tmp_path)
    outside = tmp_path / "outside-history.yaml"
    outside.write_text("attacker-seed", encoding="utf-8")
    os.symlink(outside, home / "history.yaml")

    resp = call_facade(str(home), "stats.backfill", {
        "entries": [{"media_id": "media-aaa", "int_page_read_count": 1}],
    })
    # 写入侧必须拒绝：history.yaml 是 symlink（O_NOFOLLOW 命中），不得覆盖目录项。
    assert resp["ok"] is False, resp
    assert resp["error"]["code"] in ("FORBIDDEN", "INTERNAL"), resp
    # home 外文件字节不变；symlink 目录项保留。
    assert outside.read_text(encoding="utf-8") == "attacker-seed"
    assert (home / "history.yaml").is_symlink()


def test_stats_fetch_rejects_invalid_days(tmp_path):
    home = make_home(tmp_path)
    for bad in (0, 31, -1, 1.5, "7"):
        resp = call_facade(str(home), "stats.fetch", {"days": bad})
        assert resp["ok"] is False, f"days={bad} 应被拒绝"
        assert resp["error"]["code"] == "INVALID_INPUT"


# ── 批二返修：stats.fetch 成功分支逐篇指标 + 脱敏 ──────────────────────────

def _call_fake_fetch(home: Path, days: int, daily: list[dict], token_ok: str = "1") -> dict:
    """经 stats_wechat_facade.py 伪造微信响应，走标准 facade 协议入口。"""
    env = facade_env(str(home))
    # 注入非空假凭证，触发「有凭证」真实拉取分支（数据由 fake requests 提供）。
    env["WECHAT_APPID"] = "fake-appid"
    env["WECHAT_SECRET"] = "fake-secret"
    env["FAKE_TOKEN_OK"] = token_ok
    env["FAKE_DAILY"] = json.dumps(daily, ensure_ascii=False)
    req = json.dumps({
        "protocol": 1, "requestId": "r", "operation": "stats.fetch", "payload": {"days": days},
    }, ensure_ascii=False)
    proc = subprocess.run(
        [VENV_PY, str(Path(__file__).resolve().parent / "stats_wechat_facade.py")],
        input=req + "\n", capture_output=True, text=True, env=env, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_stats_fetch_success_returns_per_article_metrics_no_credentials(tmp_path):
    """成功分支返回逐篇脱敏指标（articles），且响应中无任何凭据明文。"""
    home = make_home(tmp_path)
    _seed_history(home, [
        {"title": "表现好", "media_id": "m-good"},
        {"title": "表现差", "media_id": "m-bad"},
    ])
    daily = [{
        "list": [
            {"media_id": "m-good", "title": "表现好", "int_page_read_count": 100,
             "share_count": 5, "like_count": 3, "old_like_count": 0, "target_user": 1000},
            {"media_id": "m-bad", "title": "表现差", "int_page_read_count": 10,
             "share_count": 0, "like_count": 1, "old_like_count": 0, "target_user": 1000},
        ],
    }]
    resp = _call_fake_fetch(home, days=1, daily=daily)
    assert resp["ok"] is True, resp
    data = resp["data"]
    assert data["degraded"] is False
    assert data["backfilled"] == 2

    articles = data["articles"]
    assert len(articles) == 2
    # 顺序稳定：与微信响应 list 顺序一致。
    assert [a["media_id"] for a in articles] == ["m-good", "m-bad"]
    good, bad = articles
    assert good["stats"]["read_count"] == 100
    assert bad["stats"]["read_count"] == 10
    assert good["matched"] is True
    assert bad["matched"] is True
    # 脱敏：响应 JSON 全文不含任何凭据明文。
    dumped = json.dumps(resp, ensure_ascii=False)
    assert "fake-token" not in dumped
    assert "appid" not in dumped
    assert "secret" not in dumped
    assert "access_token" not in dumped


def test_stats_fetch_success_marks_unmatched(tmp_path):
    """微信返回了 history 里没有的 media_id → matched=false，但仍返回指标。"""
    home = make_home(tmp_path)
    _seed_history(home, [{"title": "只有这篇", "media_id": "m-known"}])
    daily = [{"list": [
        {"media_id": "m-unknown", "title": "陌生文章", "int_page_read_count": 7,
         "share_count": 0, "like_count": 0, "target_user": 100},
    ]}]
    resp = _call_fake_fetch(home, days=1, daily=daily)
    assert resp["ok"] is True, resp
    data = resp["data"]
    assert data["backfilled"] == 0
    assert data["articles"][0]["matched"] is False
    assert data["articles"][0]["stats"]["read_count"] == 7


# ── 批二返修：每日摘要响应逐日校验，失败即业务失败且不写历史 ────────────────

def test_stats_fetch_daily_errcode_fails_and_does_not_write_history(tmp_path):
    """首日成功、次日 errcode=45009：整批业务失败，history.yaml 字节不变。"""
    home = make_home(tmp_path)
    _seed_history(home, [{"title": "第一篇", "media_id": "m-a"}])
    before = (home / "history.yaml").read_bytes()

    daily = [
        {"list": [{"media_id": "m-a", "title": "第一篇", "int_page_read_count": 100,
                   "share_count": 0, "like_count": 0, "target_user": 1000}]},
        {"errcode": 45009, "errmsg": "reach max api daily quota limit"},
    ]
    resp = _call_fake_fetch(home, days=2, daily=daily)
    assert resp["ok"] is False, resp
    assert resp["error"]["code"] == "CAPABILITY_UNAVAILABLE", resp
    assert "45009" in resp["error"]["message"] or "errcode" in resp["error"]["message"]
    # 失败路径不得写 history.yaml（半份数据比没有更糟）。
    assert (home / "history.yaml").read_bytes() == before


def test_stats_fetch_all_success_positive(tmp_path):
    """全部成功正向用例：防过度收紧。"""
    home = make_home(tmp_path)
    _seed_history(home, [{"title": "第一篇", "media_id": "m-a"}])
    daily = [{"list": [{"media_id": "m-a", "title": "第一篇", "int_page_read_count": 50,
                        "share_count": 2, "like_count": 1, "target_user": 500}]}]
    resp = _call_fake_fetch(home, days=1, daily=daily)
    assert resp["ok"] is True, resp
    assert resp["data"]["backfilled"] == 1


def test_stats_fetch_daily_non_dict_fails(tmp_path):
    """每日响应不是 dict → 业务失败。"""
    home = make_home(tmp_path)
    _seed_history(home, [{"title": "第一篇", "media_id": "m-a"}])
    resp = _call_fake_fetch(home, days=1, daily=["not-a-dict"])
    assert resp["ok"] is False, resp
    assert resp["error"]["code"] == "CAPABILITY_UNAVAILABLE"


# ── 批二返修：回填读-改-写在同一把 home 锁内（并发不丢更新）─────────────────

def _call_backfill_hold(home: Path, entries: list[dict], hold_ms: int) -> dict:
    """经 backfill_hold_facade.py 注入读后延迟，走标准 facade 协议入口。"""
    env = facade_env(str(home))
    env["BACKFILL_HOLD_MS"] = str(hold_ms)
    req = json.dumps({
        "protocol": 1, "requestId": "r", "operation": "stats.backfill", "payload": {"entries": entries},
    }, ensure_ascii=False)
    proc = subprocess.run(
        [VENV_PY, str(Path(__file__).resolve().parent / "backfill_hold_facade.py")],
        input=req + "\n", capture_output=True, text=True, env=env, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_stats_backfill_concurrent_no_lost_update(tmp_path):
    """两个子进程并发回填不同 media_id：最终 history.yaml 两项 stats 都在，无 .tmp 残留。"""
    home = make_home(tmp_path)
    _seed_history(home, [
        {"title": "甲篇", "media_id": "m-a"},
        {"title": "乙篇", "media_id": "m-b"},
    ])

    import threading
    results: dict[str, dict] = {}

    def worker(name: str, media_id: str) -> None:
        results[name] = _call_backfill_hold(home, [{
            "media_id": media_id, "title": name, "int_page_read_count": 100,
            "share_count": 1, "like_count": 1, "target_user": 1000,
        }], hold_ms=400)

    t1 = threading.Thread(target=worker, args=("甲篇", "m-a"))
    t2 = threading.Thread(target=worker, args=("乙篇", "m-b"))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert results["甲篇"]["ok"] is True, results
    assert results["乙篇"]["ok"] is True, results

    on_disk = yaml.safe_load((home / "history.yaml").read_text(encoding="utf-8"))
    by_media = {a["media_id"]: a for a in on_disk["articles"] if "media_id" in a}
    assert "stats" in by_media["m-a"], "m-a 的 stats 丢失（并发丢更新）"
    assert "stats" in by_media["m-b"], "m-b 的 stats 丢失（并发丢更新）"
    # 并发期间无残留 .tmp 文件。
    leftovers = [p for p in home.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == []


def test_rewrite_score_returns_quality_score(tmp_path):
    home = make_home(tmp_path)
    text = "这是一篇内容完整、表达自然、读者能获得明确结论的公众号文章正文。"
    resp = call_facade(str(home), "rewrite.score", {"text": text})
    assert resp["ok"] is True, resp
    assert isinstance(resp["data"]["quality_score"], (int, float))
    assert isinstance(resp["data"]["param_scores"], dict)
    # 纯本地计算：不写任何文件。
    assert sorted(p.name for p in home.iterdir()) == []


def test_rewrite_score_rejects_non_string_text(tmp_path):
    home = make_home(tmp_path)
    resp = call_facade(str(home), "rewrite.score", {"text": 123})
    assert resp["ok"] is False
    assert resp["error"]["code"] == "INVALID_INPUT"


def test_rewrite_similarity_returns_bounded_value(tmp_path):
    home = make_home(tmp_path)
    source = "源稿内容甲乙丙丁戊己庚辛壬癸，这是一段完全不同的公众号长句表达。"
    variants = [
        "改写版本一：完全重写了结构与表达，内容级真改而不是洗稿，口语化短句分段。",
        "改写版本二：另一个平台版本，换了开头与信息顺序，人设一致但表达方式适配。",
    ]
    resp = call_facade(str(home), "rewrite.similarity", {"source": source, "variants": variants})
    assert resp["ok"] is True, resp
    assert 0.0 <= resp["data"]["max_similarity"] <= 1.0
    assert resp["data"]["param"] == "similarity_threshold"
    assert len(resp["data"]["variants"]) == 2
    # 纯本地计算：不写任何文件。
    assert sorted(p.name for p in home.iterdir()) == []


def test_rewrite_similarity_identical_text_is_one(tmp_path):
    home = make_home(tmp_path)
    text = "完全相同的内容完全相同的表达完全相同的结构"
    resp = call_facade(str(home), "rewrite.similarity", {"source": text, "variants": [text]})
    assert resp["ok"] is True, resp
    assert resp["data"]["max_similarity"] == 1.0


def test_rewrite_similarity_rejects_non_list_variants(tmp_path):
    home = make_home(tmp_path)
    resp = call_facade(str(home), "rewrite.similarity", {"source": "x", "variants": "not-list"})
    assert resp["ok"] is False
    assert resp["error"]["code"] == "INVALID_INPUT"
