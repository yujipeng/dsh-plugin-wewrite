"""JEE-1956 推送栅栏缺口回归（每条先红后绿）。

覆盖 issue 缺口 1/2/4 的复现路径：
1. 摘要复验基线——prepare 固定 reportVersion/publishable/coverHash/themeId/accountRef/accountVersion，
   发送二次复验逐项比对（改审稿/改封面/改主题后发送必须拒）。
2. TTL fail-closed——expiresAt 缺失/空串/数值类型一律拒。
4. 旧格式整体迁移——定向恢复一条后，其余旧条目也整体迁 schemaVersion:2。

这些用例在 JEE-1956 修复前（main@3292895）会红。
"""

from __future__ import annotations

import base64
import json
import struct
import zlib

from conftest import call_facade, create_run, make_home, make_publishable_run, write_article


def _png_bytes() -> bytes:
    sig = b"\x89PNG\r\n\x1a\n"

    def chunk(t: bytes, d: bytes) -> bytes:
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00\xff\x00\x00")
    return sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


def _publish(home, run_id, generation, workflow="wf-a", expected=None):
    return call_facade(str(home), "binding.publish", {
        "requestId": f"b{generation}",
        "runId": run_id,
        "workflowId": workflow,
        "generation": generation,
        "agentId": f"agent-{generation}",
        "sessionId": f"session-{generation}",
        "workspaceId": "ws-1",
        "phase": "writing",
        **({"expectedGeneration": expected} if expected is not None else {}),
    })


def _put_cover(home, run_id):
    b64 = base64.b64encode(_png_bytes()).decode("ascii")
    return call_facade(str(home), "asset.put", {
        "requestId": "cover-1",
        "runId": run_id,
        "role": "cover",
        "mediaType": "image/png",
        "base64": b64,
        "expectedArticleVersion": None,
    })


def _write_review(home, run_id, article_version, publishable):
    report = {
        "version": 1,
        "decision": "pass" if publishable else "revise",
        "pass_number": 1,
        "publishable": publishable,
        "dimensions": {"accuracy": 5, "viewpoint": 5, "usefulness": 5, "voice": 5, "readability": 5},
        "dimension_average": 5.0,
        "blockers": [],
        "major_issues": [],
        "notes": "",
        "draft_chars": 3,
        "final_chars": 3,
        "edit_ratio": 0.0,
        "structure_changed": False,
        "article_hash": article_version,
    }
    (home / "runs" / run_id / "review-report.json").write_text(
        json.dumps(report, ensure_ascii=False), encoding="utf-8"
    )


def _prepare(home, run_id, cover_asset_id, theme="professional-clean"):
    return call_facade(str(home), "draft.prepare", {
        "requestId": "p1",
        "runId": run_id,
        "expectedVersion": None,
        "coverAssetId": cover_asset_id,
        "themeId": theme,
    })


def _send(home, run_id, intent_id, version, workflow="wf-a", generation=1):
    return call_facade(str(home), "draft.send", {
        "requestId": "s1",
        "runId": run_id,
        "intentId": intent_id,
        "expectedVersion": version,
        "context": {"runId": run_id, "workflowId": workflow, "generation": generation},
    })


def _intent(home, run_id, intent_id):
    p = home / "runs" / run_id / ".dsh-workbench" / "intents.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    return [i for i in data["intents"] if i["intentId"] == intent_id][0]


def _setup(home, run_id):
    version, cover = make_publishable_run(home, run_id)
    return version, cover


def _send_inproc(dispatch, run_id, intent_id, version):
    """进程内驱动 draft_dispatch.send，拒绝时把 FacadeError 归一为业务联合（对齐子进程协议）。"""
    from dsh_wewrite_facade.errors import FacadeError

    try:
        return dispatch.send(
            {"requestId": "s", "runId": run_id, "intentId": intent_id, "expectedVersion": version},
            {"runId": run_id, "workflowId": "wf-a", "generation": 1},
        )
    except FacadeError as exc:
        return {"ok": False, "error": {"code": exc.code, "message": str(exc)}}


# ── 缺口 1：摘要复验基线 ─────────────────────────────────────────────────

def test_prepare_pins_summary_baseline(tmp_path):
    """prepare 真正固定 reportVersion/publishable/coverHash/themeId/accountRef/accountVersion。"""
    home = make_home(tmp_path)
    run_id = create_run(home)
    version, cover = _setup(home, run_id)

    prep = _prepare(home, run_id, cover["assetId"])
    assert prep["ok"] is True, prep
    item = _intent(home, run_id, prep["data"]["intentId"])
    # 摘要字段必须非空/真实（不再是 None/空串基线缺失）。
    assert isinstance(item["reportVersion"], str) and len(item["reportVersion"]) == 64, item
    assert item["publishable"] is True, item
    assert item["coverHash"] == cover["contentHash"], item
    assert item["themeId"] == "professional-clean", item
    assert "accountRef" in item and "accountVersion" in item, item


def test_send_rejects_review_changed_after_prepare(tmp_path):
    """缺口 1 实测复现：改审稿为不通过后发送仍成功（红：当前会成功）。"""
    home = make_home(tmp_path)
    run_id = create_run(home)
    version, cover = _setup(home, run_id)
    prep = _prepare(home, run_id, cover["assetId"])
    intent_id = prep["data"]["intentId"]

    # 审稿改为不通过（正文绑定不变）。
    _write_review(home, run_id, version, False)

    resp = _send(home, run_id, intent_id, version)
    assert resp["ok"] is False, resp
    assert resp["error"]["code"] in ("CANCELLED", "VERSION_CONFLICT", "FORBIDDEN"), resp
    # adapter 未被调用：失败响应不携带成功 data/counters。
    assert "counters" not in (resp.get("data") or {})


def test_send_rejects_cover_changed_after_prepare(tmp_path):
    """缺口 1：prepare 后封面登记哈希变化（更换/篡改），发送二次复验拒绝。"""
    home = make_home(tmp_path)
    run_id = create_run(home)
    version, cover = _setup(home, run_id)
    prep = _prepare(home, run_id, cover["assetId"])
    intent_id = prep["data"]["intentId"]

    # 更换封面：把登记项 contentHash 篡改为另一内容（模拟封面被替换）。
    manifest = home / "runs" / run_id / "assets" / "manifest.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    for a in data["assets"]:
        if a["assetId"] == cover["assetId"]:
            a["contentHash"] = "0" * 64
    manifest.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    resp = _send(home, run_id, intent_id, version)
    assert resp["ok"] is False, resp
    assert resp["error"]["code"] in ("CANCELLED", "VERSION_CONFLICT", "FORBIDDEN"), resp


# ── 缺口 2：TTL fail-closed ─────────────────────────────────────────────

def test_send_rejects_missing_expires_at(tmp_path):
    """缺口 2 实测复现：删除期限后发送成功（红：当前会成功）。"""
    home = make_home(tmp_path)
    run_id = create_run(home)
    version, cover = _setup(home, run_id)
    prep = _prepare(home, run_id, cover["assetId"])
    intent_id = prep["data"]["intentId"]

    p = home / "runs" / run_id / ".dsh-workbench" / "intents.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    for i in data["intents"]:
        if i["intentId"] == intent_id:
            del i["expiresAt"]
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    resp = _send(home, run_id, intent_id, version)
    assert resp["ok"] is False, resp
    assert resp["error"]["code"] == "CANCELLED", resp


def test_send_rejects_empty_or_nonstring_expires_at(tmp_path):
    """缺口 2：expiresAt 空串/数值类型一律拒（fail-closed）。"""
    home = make_home(tmp_path)
    run_id = create_run(home)
    version, cover = _setup(home, run_id)
    prep = _prepare(home, run_id, cover["assetId"])
    intent_id = prep["data"]["intentId"]
    p = home / "runs" / run_id / ".dsh-workbench" / "intents.json"

    for bad in ("", 9999999999):
        data = json.loads(p.read_text(encoding="utf-8"))
        for i in data["intents"]:
            if i["intentId"] == intent_id:
                i["expiresAt"] = bad
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        resp = _send(home, run_id, intent_id, version)
        assert resp["ok"] is False, resp
        assert resp["error"]["code"] == "CANCELLED", resp


# ── 缺口 4：旧格式整体迁移 ─────────────────────────────────────────────

def test_recover_migrates_all_legacy_entries(tmp_path):
    """缺口 4 实测复现：定向恢复第一条后第二条仍无 state（红：当前只迁目标）。"""
    home = make_home(tmp_path)
    run_id = create_run(home)
    version = write_article(home, run_id, "# 标题\n\n正文内容")
    assert _publish(home, run_id, 1)["ok"] is True

    wb = home / "runs" / run_id / ".dsh-workbench"
    wb.mkdir(parents=True, exist_ok=True)
    legacy = [
        {"intentId": "i1", "runId": run_id, "workflowId": "wf-a", "generation": 1,
         "articleVersion": version, "expiresAt": "9999999999"},
        {"intentId": "i2", "runId": run_id, "workflowId": "wf-a", "generation": 1,
         "articleVersion": version, "expiresAt": "9999999999"},
    ]
    (wb / "intents.json").write_text(
        json.dumps({"schemaVersion": 1, "intents": legacy}, ensure_ascii=False), encoding="utf-8"
    )

    rec = call_facade(str(home), "draft.recover", {"requestId": "r1", "runId": run_id, "intentId": "i1"})
    assert rec["ok"] is True, rec

    data = json.loads((wb / "intents.json").read_text(encoding="utf-8"))
    assert data["schemaVersion"] == 2, data
    # 整体迁移：两条都必须是 v2 合法状态，而非只有定向的那条。
    for i in data["intents"]:
        assert i.get("state") == "unknown", i


# ── 返修真红自证（进程内 monkeypatch，复验者可自助复现红转绿）────────────────

def test_red_probe_summary_reverify_is_load_bearing(tmp_path, monkeypatch):
    """缺口 1 自证：同一场景内「修复在位→拒」与「注入回退→发送成功」两相。

    复验者跑本用例即可自助复核红转绿：monkeypatch 把 _verify_summary 回退为恒真后，
    改审稿的发送恢复成功——证明该二次复验是 load-bearing，去掉即绕过。
    """
    from dsh_wewrite_facade import draft_dispatch

    home = make_home(tmp_path)
    run_id = create_run(home)
    version, cover = _setup(home, run_id)
    monkeypatch.setenv("WEWRITE_HOME", str(home))

    def new_intent():
        prep = _prepare(home, run_id, cover["assetId"])
        assert prep["ok"] is True, prep
        return prep["data"]["intentId"]

    # 相 1：修复在位，改审稿后发送必须被拒。
    iid_a = new_intent()
    _write_review(home, run_id, version, False)
    resp_a = _send_inproc(draft_dispatch, run_id, iid_a, version)
    assert resp_a["ok"] is False, resp_a

    # 相 2：注入回退（等价于修复前无此项比对），改审稿后发送恢复成功。
    # S4 prepare 要求审稿通过，故先恢复通过→prepare，再改不通过→发送。
    monkeypatch.setattr(draft_dispatch, "_verify_summary", lambda *a, **k: True)
    _write_review(home, run_id, version, True)
    iid_b = new_intent()
    _write_review(home, run_id, version, False)
    resp_b = _send_inproc(draft_dispatch, run_id, iid_b, version)
    assert resp_b["ok"] is True, resp_b
    assert resp_b["data"]["status"] == "succeeded"


def test_red_probe_ttl_failclosed_is_load_bearing(tmp_path, monkeypatch):
    """缺口 2 自证：同一场景内「修复在位→拒」与「注入回退→发送成功」两相。"""
    from dsh_wewrite_facade import draft_dispatch

    home = make_home(tmp_path)
    run_id = create_run(home)
    version, cover = _setup(home, run_id)
    monkeypatch.setenv("WEWRITE_HOME", str(home))

    def new_intent():
        prep = _prepare(home, run_id, cover["assetId"])
        assert prep["ok"] is True, prep
        return prep["data"]["intentId"]

    def strip_expires(intent_id):
        p = home / "runs" / run_id / ".dsh-workbench" / "intents.json"
        data = json.loads(p.read_text(encoding="utf-8"))
        for i in data["intents"]:
            if i["intentId"] == intent_id:
                del i["expiresAt"]
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    # 相 1：修复在位，删除期限后发送必须被拒。
    iid_a = new_intent()
    strip_expires(iid_a)
    resp_a = _send_inproc(draft_dispatch, run_id, iid_a, version)
    assert resp_a["ok"] is False, resp_a

    # 相 2：注入回退（TTL 恒真，等价于修复前仅非空字符串才检查），发送恢复成功。
    monkeypatch.setattr(draft_dispatch, "_verify_ttl", lambda item: True)
    iid_b = new_intent()
    strip_expires(iid_b)
    resp_b = _send_inproc(draft_dispatch, run_id, iid_b, version)
    assert resp_b["ok"] is True, resp_b
    assert resp_b["data"]["status"] == "succeeded"
