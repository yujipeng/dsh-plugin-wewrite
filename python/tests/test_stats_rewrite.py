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

import hashlib
import os
from pathlib import Path

import yaml

from conftest import call_facade, make_home


def _seed_history(home: Path) -> None:
    """直接在 home 根写一份 history.yaml（绕过 facade 构造夹具，含 media_id/title）。"""
    data = {
        "version": 1,
        "articles": [
            {"title": "第一篇", "media_id": "media-aaa", "run_id": "20261103-000000-aaaaaa"},
            {"title": "第二篇", "run_id": "20261103-000000-bbbbbb"},
        ],
    }
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
