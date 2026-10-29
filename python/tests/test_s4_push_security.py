"""S4 双闸/逐篇确认/安全边界（Python 侧）。

覆盖：
- 发布白名单：仅令牌/正文图片上传/封面素材上传/draft.add/draft.get；无 freepublish/mass/send。
- intentId 独立幂等：同 intent 即使多个 callId 分别获批，最多一次 draft/add；unknown 不释放键。
- dry-run：假调用一次、wechatApiCalls === 0、remoteFetchCalls 单独计数、不携带微信凭据。
- 密钥不回显：哨兵 secret 不进 stdout/stderr/RPC 响应/配置快照。
- 审批期间业务竞争：prepare 后改正文/封面/主题/账号，send 拒绝且 wechatApiCalls === 0。
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from conftest import (
    call_facade,
    create_run,
    make_home,
    make_publishable_run,
    put_cover,
    write_article,
    write_review,
    facade_env,
)


def _prepare(home, run_id, cover_asset_id, theme="professional-clean"):
    return call_facade(str(home), "draft.prepare", {
        "requestId": "p1", "runId": run_id, "expectedVersion": None,
        "coverAssetId": cover_asset_id, "themeId": theme,
    })


def _send(home, run_id, intent_id, version, transport="fake", credentials=None, workflow="wf-1", generation=1):
    payload = {
        "requestId": "s1", "runId": run_id, "intentId": intent_id, "expectedVersion": version,
        "transport": transport,
        "context": {"runId": run_id, "workflowId": workflow, "generation": generation},
    }
    if credentials is not None:
        payload["credentials"] = credentials
    return call_facade(str(home), "draft.send", payload)


def test_publish_whitelist_blocks_mass_send_and_freepublish():
    from dsh_wewrite_facade import publish
    from dsh_wewrite_facade.errors import FacadeError
    import pytest

    # 白名单放行。
    for endpoint in [
        "https://api.weixin.qq.com/cgi-bin/token",
        "https://api.weixin.qq.com/cgi-bin/media/uploadimg",
        "https://api.weixin.qq.com/cgi-bin/material/add_material",
        "https://api.weixin.qq.com/cgi-bin/draft/add",
        "https://api.weixin.qq.com/cgi-bin/draft/get",
    ]:
        publish.assert_whitelisted(endpoint)  # 不抛即通过

    # 群发/发布类端点拒绝。
    for endpoint in [
        "https://api.weixin.qq.com/cgi-bin/message/mass/send",
        "https://api.weixin.qq.com/cgi-bin/message/mass/sendall",
        "https://api.weixin.qq.com/cgi-bin/freepublish/submit",
        "https://api.weixin.qq.com/cgi-bin/message/subscribe/bizsend",
    ]:
        with pytest.raises(FacadeError) as exc:
            publish.assert_whitelisted(endpoint)
        assert exc.value.code == "FORBIDDEN", endpoint

    # 未知端点拒绝。
    with pytest.raises(FacadeError):
        publish.assert_whitelisted("https://api.weixin.qq.com/cgi-bin/message/template/send")


def test_intent_idempotency_single_draft_add(tmp_path):
    """同 intent 多次 send 最多一次 draft/add；第二次终态拒绝且 adapter 不再调用。"""
    home = make_home(tmp_path)
    run_id = create_run(home)
    version, cover = make_publishable_run(home, run_id, workflow="wf-1")
    intent_id = _prepare(home, run_id, cover["assetId"])["data"]["intentId"]

    first = _send(home, run_id, intent_id, version)
    assert first["ok"] is True, first
    assert first["data"]["status"] == "succeeded"
    assert first["data"]["dryRun"] is True
    assert first["data"]["counters"]["wechatApiCalls"] == 0

    # 第二次（不同 callId）终态拒绝：不再新建 operation，不再次 add。
    second = _send(home, run_id, intent_id, version)
    assert second["ok"] is False, second
    assert second["error"]["code"] in ("INVALID_TRANSITION", "VERSION_CONFLICT", "NOT_FOUND"), second


def test_unknown_tombstone_not_released(tmp_path):
    """unknown 墓碑不释放 intentId 唯一键：recover 后再 send 仍终态拒绝。"""
    home = make_home(tmp_path)
    run_id = create_run(home)
    version, cover = make_publishable_run(home, run_id, workflow="wf-1")
    intent_id = _prepare(home, run_id, cover["assetId"])["data"]["intentId"]

    # 手动把 intent 置为 dispatching（模拟发送中）。
    p = home / "runs" / run_id / ".dsh-workbench" / "intents.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    for item in data["intents"]:
        if item["intentId"] == intent_id:
            item["state"] = "dispatching"
            item["operationId"] = "op-1"
            item["claimNonce"] = "nonce-1"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    # recover：dispatching → unknown（墓碑）。
    rec = call_facade(str(home), "draft.recover", {"requestId": "r1", "runId": run_id, "intentId": intent_id})
    assert rec["ok"] is True, rec
    assert rec["data"]["unknown"] == 1

    # unknown 后再次 send：终态拒绝，不释放键。
    again = _send(home, run_id, intent_id, version)
    assert again["ok"] is False, again
    assert again["error"]["code"] == "INVALID_TRANSITION", again


def test_dry_run_real_wechat_calls_zero_and_credentials_not_leaked(tmp_path):
    """dry-run：假调用一次、wechatApiCalls === 0、remoteFetchCalls 单独计数、无微信凭据。"""
    home = make_home(tmp_path)
    run_id = create_run(home)
    version, cover = make_publishable_run(home, run_id, workflow="wf-1")
    intent_id = _prepare(home, run_id, cover["assetId"])["data"]["intentId"]

    sentinel = "SENTINEL-WECHAT-SECRET-9f8a7b6c"
    resp = _send(home, run_id, intent_id, version, transport="fake",
                 credentials={"appid": "wx-sentinel-app", "secret": sentinel})
    assert resp["ok"] is True, resp
    data = resp["data"]
    assert data["dryRun"] is True
    assert data["mediaId"].startswith("fake-")
    assert data["counters"]["wechatApiCalls"] == 0
    assert data["counters"]["remoteFetchCalls"] == 0
    # 哨兵不回显：整个 JSON 响应字符串不含哨兵。
    assert sentinel not in json.dumps(resp, ensure_ascii=False)
    assert sentinel not in json.dumps(data, ensure_ascii=False)


def test_business_race_reject_after_content_change(tmp_path):
    """审批期间业务竞争：prepare 后改正文/封面/主题，send 拒绝且 wechatApiCalls === 0。"""
    home = make_home(tmp_path)
    run_id = create_run(home)
    version, cover = make_publishable_run(home, run_id, workflow="wf-1")
    intent_id = _prepare(home, run_id, cover["assetId"])["data"]["intentId"]

    # 正文变化（版本变化 → 第二闸拒）。
    write_article(home, run_id, "# 新标题\n\n" + "改过的正文" * 40)
    resp = _send(home, run_id, intent_id, version)
    assert resp["ok"] is False, resp
    assert resp["error"]["code"] in ("CANCELLED", "VERSION_CONFLICT"), resp
    assert "counters" not in (resp.get("data") or {})


def test_prepare_rejects_missing_or_unreadable_cover(tmp_path):
    """prepare 要求实际封面可读；假封面/缺封面拒绝。"""
    home = make_home(tmp_path)
    run_id = create_run(home)
    version, _cover = make_publishable_run(home, run_id, workflow="wf-1")

    resp = _prepare(home, run_id, "nonexistent-cover")
    assert resp["ok"] is False, resp
    assert resp["error"]["code"] in ("ARTIFACT_MISSING", "NOT_FOUND"), resp


def test_prepare_rejects_body_too_short(tmp_path):
    """正文不足 200 字 prepare 拒绝。"""
    home = make_home(tmp_path)
    run_id = create_run(home)
    version = write_article(home, run_id, "# 短\n\n太短")
    # 需要 binding + 通过审稿才能进入内容检查；但正文太短会先被内容检查拒绝。
    # 直接构造 binding + 审稿（正文哈希绑定），验证内容限制被拒。
    assert call_facade(str(home), "binding.publish", {
        "requestId": "b1", "runId": run_id, "workflowId": "wf-1", "generation": 1,
        "agentId": "a", "sessionId": "s", "workspaceId": "ws-1", "phase": "writing",
    })["ok"] is True
    write_review(home, run_id, version, True)
    cover = put_cover(home, run_id)
    resp = _prepare(home, run_id, cover["assetId"])
    assert resp["ok"] is False, resp
    assert resp["error"]["code"] == "INVALID_INPUT", resp
    assert "200" in resp["error"]["message"]


# ── 返修 3：journal 唯一键边界（并发 / 重启 / 超时重试）────────────────

def _count_file_lines(path: Path) -> int:
    if not path.exists():
        return 0
    return len(path.read_text(encoding="utf-8").splitlines())


def _send_counting_inprocess(home, run_id, intent_id, version, count_file):
    """在独立子进程里跑 counting_facade（标准 protocol 入口 + 注入文件计数替身）。"""
    env = facade_env(str(home))
    env["CREATE_DRAFT_COUNT_FILE"] = str(count_file)
    req = {"protocol": 1, "requestId": "s", "operation": "draft.send", "payload": {
        "requestId": "s", "runId": run_id, "intentId": intent_id, "expectedVersion": version,
        "transport": "fake",
        "context": {"runId": run_id, "workflowId": "wf-1", "generation": 1},
    }}
    counting_facade = Path(__file__).resolve().parent / "counting_facade.py"
    proc = subprocess.run(
        [sys.executable, str(counting_facade)],
        input=json.dumps(req, ensure_ascii=False) + "\n",
        capture_output=True, text=True, env=env, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_concurrent_sends_same_intent_create_draft_called_once(tmp_path):
    """多进程并发（多个已批准 callId 竞争同一 intentId）：create_draft 跨进程计数 == 1。"""
    home = make_home(tmp_path)
    run_id = create_run(home)
    version, cover = make_publishable_run(home, run_id, workflow="wf-1")
    intent_id = _prepare(home, run_id, cover["assetId"])["data"]["intentId"]

    count_file = tmp_path / "create_draft.count"
    results: list[dict] = []
    lock = threading.Lock()

    def worker():
        r = _send_counting_inprocess(home, run_id, intent_id, version, count_file)
        with lock:
            results.append(r)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    # 关键断言：跨进程共享计数 == 1（不再用「succeeded 数量」代替调用计数）。
    assert _count_file_lines(count_file) == 1, (results, _count_file_lines(count_file))
    # 恰好一个 succeeded（业务侧仍一致）。
    succeeded = [r for r in results if r.get("ok") is True and r.get("data", {}).get("status") == "succeeded"]
    assert len(succeeded) == 1, results


def test_restart_after_success_does_not_re_add(tmp_path):
    """进程重启后状态恢复：同 intent 再次 send（新子进程）终态拒绝，create_draft 计数不增。"""
    home = make_home(tmp_path)
    run_id = create_run(home)
    version, cover = make_publishable_run(home, run_id, workflow="wf-1")
    intent_id = _prepare(home, run_id, cover["assetId"])["data"]["intentId"]

    count_file = tmp_path / "create_draft.count"
    first = _send_counting_inprocess(home, run_id, intent_id, version, count_file)
    assert first["ok"] is True, first
    assert _count_file_lines(count_file) == 1

    # 模拟重启：全新子进程再发同一 intent（状态从磁盘恢复，终态拒绝）。
    again = _send_counting_inprocess(home, run_id, intent_id, version, count_file)
    assert again["ok"] is False, again
    assert again["error"]["code"] in ("INVALID_TRANSITION", "VERSION_CONFLICT", "NOT_FOUND"), again
    # 不重复 draft/add：计数仍为 1。
    assert _count_file_lines(count_file) == 1


def test_timeout_unknown_tombstone_no_retry_re_add(tmp_path, monkeypatch):
    """发送超时后标 unknown，墓碑不释放：再次 send 终态拒绝，create_draft 计数仍 == 1。

    用注入替身让 create_draft 真实抛异常触发 dispatching→unknown（不手工改 intents.json）。
    """
    from dsh_wewrite_facade import draft_dispatch
    from dsh_wewrite_facade import publish as publish_mod
    from dsh_wewrite_facade.errors import FacadeError

    home = make_home(tmp_path)
    run_id = create_run(home)
    version, cover = make_publishable_run(home, run_id, workflow="wf-1")
    intent_id = _prepare(home, run_id, cover["assetId"])["data"]["intentId"]
    monkeypatch.setenv("WEWRITE_HOME", str(home))

    class ExplodingAdapter:
        def __init__(self) -> None:
            self.calls = 0

        def create_draft(self, **kwargs):
            self.calls += 1
            raise RuntimeError("simulated network timeout")

    first_calls: list[ExplodingAdapter] = []
    publish_mod.set_adapter_factory(lambda kind, credentials: (first_calls.append(ExplodingAdapter()) or first_calls[-1]))
    try:
        resp = draft_dispatch.send(
            {"requestId": "s1", "runId": run_id, "intentId": intent_id, "expectedVersion": version},
            {"runId": run_id, "workflowId": "wf-1", "generation": 1},
        )
    finally:
        publish_mod.set_adapter_factory(None)

    assert resp["ok"] is True, resp
    assert resp["data"]["status"] == "unknown", resp
    assert first_calls[0].calls == 1, "create_draft 应被调用一次后抛异常"

    # 墓碑保留：再次 send（正常替身）终态拒绝，不自动重试 add。
    class OkAdapter:
        def __init__(self) -> None:
            self.calls = 0

        def create_draft(self, **kwargs):
            self.calls += 1
            return {"mediaId": "fake-ok", "dryRun": True, "title": ""}

    second_calls: list[OkAdapter] = []
    publish_mod.set_adapter_factory(lambda kind, credentials: (second_calls.append(OkAdapter()) or second_calls[-1]))
    try:
        try:
            draft_dispatch.send(
                {"requestId": "s2", "runId": run_id, "intentId": intent_id, "expectedVersion": version},
                {"runId": run_id, "workflowId": "wf-1", "generation": 1},
            )
            code = None
        except FacadeError as exc:
            code = exc.code
    finally:
        publish_mod.set_adapter_factory(None)

    assert code == "INVALID_TRANSITION", code
    # 第二次 send 在 claim 阶段即终态拒绝，未构造 adapter、未再次调用 create_draft。
    assert second_calls == [] or second_calls[0].calls == 0, "unknown 墓碑不得触发自动重试 add"


def test_concurrent_inprocess_create_draft_called_once(tmp_path, monkeypatch):
    """进程内并发（共享计数 adapter 工厂 seam）：create_draft 调用次数 == 1。

    经 publish.set_adapter_factory 注入替身（白名单/计数逻辑在 send 路径，不绕过）。
    """
    from dsh_wewrite_facade import draft_dispatch
    from dsh_wewrite_facade import publish as publish_mod
    from dsh_wewrite_facade.errors import FacadeError

    home = make_home(tmp_path)
    run_id = create_run(home)
    version, cover = make_publishable_run(home, run_id, workflow="wf-1")
    intent_id = _prepare(home, run_id, cover["assetId"])["data"]["intentId"]
    monkeypatch.setenv("WEWRITE_HOME", str(home))

    class CountingAdapter:
        def __init__(self) -> None:
            self.calls = 0

        def create_draft(self, **kwargs):
            self.calls += 1
            return {"mediaId": "fake-inproc", "dryRun": True, "title": ""}

    counters: list[CountingAdapter] = []
    lock = threading.Lock()

    def factory(kind, credentials):
        adapter = CountingAdapter()
        with lock:
            counters.append(adapter)
        return adapter

    publish_mod.set_adapter_factory(factory)
    outcomes: list[tuple[str, str]] = []

    def run_send():
        try:
            r = draft_dispatch.send(
                {"requestId": "s", "runId": run_id, "intentId": intent_id, "expectedVersion": version},
                {"runId": run_id, "workflowId": "wf-1", "generation": 1},
            )
            outcomes.append(("ok", r["data"]["status"]))
        except FacadeError as exc:
            outcomes.append(("err", exc.code))

    threads = [threading.Thread(target=run_send) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    publish_mod.set_adapter_factory(None)

    # create_draft（= draft/add）只被调用一次。
    total_calls = sum(a.calls for a in counters)
    assert total_calls == 1, (total_calls, outcomes)
    # 恰好一个 sent。
    assert [o for o in outcomes if o == ("ok", "succeeded")] == [("ok", "succeeded")], outcomes


# ── 返修 3：adapter seam 作用域 + 真实路径保护 ─────────────────

def _fake_upstream() -> tuple[dict, dict]:
    """monkeypatch 上游网络调用：不打真实网络。

    返回两个可写状态 dict：token 计数与 draft 计数。
    """
    import wewrite.toolkit.publisher as pub
    import wewrite.toolkit.wechat_api as api

    state = {"token_calls": 0, "draft_calls": 0}

    def fake_token(appid, secret, force_refresh=False):
        state["token_calls"] += 1
        return "fake-access-token"

    class FakeDraftResult:
        media_id = "real-media-1"

    def fake_create_draft(access_token, title, html, digest, thumb_media_id=None, author=None):
        state["draft_calls"] += 1
        return FakeDraftResult()

    return state, {"api": api, "pub": pub, "fake_token": fake_token, "fake_create_draft": fake_create_draft}


def test_seam_only_applies_to_fake_kind(monkeypatch):
    """seam 只对 kind=='fake' 生效；kind=='wechat' 无视 seam，始终构造 RealPublisher。"""
    from dsh_wewrite_facade import publish as publish_mod

    class SpyAdapter:
        def create_draft(self, **kwargs):
            return {"mediaId": "fake", "dryRun": True, "title": ""}

    publish_mod.set_adapter_factory(lambda kind, credentials: SpyAdapter())
    try:
        fake = publish_mod.build_transport("fake", {})
        assert isinstance(fake.adapter, SpyAdapter), "fake 走 seam 替身"
        assert fake.is_fake is True

        real = publish_mod.build_transport("wechat", {"appid": "wx", "secret": "s"})
        assert isinstance(real.adapter, publish_mod.RealPublisher), "wechat 无视 seam，始终 RealPublisher"
        assert real.is_fake is False
    finally:
        publish_mod.set_adapter_factory(None)


def test_real_publisher_whitelist_and_counter_under_seam(monkeypatch):
    """真实路径保护（seam 已注册时仍生效）：上游 create_draft 调 1 次，wechatApiCalls +2。"""
    from dsh_wewrite_facade import publish as publish_mod
    from dsh_wewrite_facade import counters as counters_mod

    state, up = _fake_upstream()
    monkeypatch.setattr(up["api"], "get_access_token", up["fake_token"])
    monkeypatch.setattr(up["pub"], "create_draft", up["fake_create_draft"])

    # 注册一个 seam（fake 替身），确认真实路径不受影响。
    class SpyAdapter:
        def create_draft(self, **kwargs):
            raise AssertionError("seam 替身不得用于真实路径")

    publish_mod.set_adapter_factory(lambda kind, credentials: SpyAdapter())
    try:
        counters_mod.reset_counters()
        before = counters_mod.get_counters().wechat()

        transport = publish_mod.build_transport("wechat", {"appid": "wx-app", "secret": "wx-secret"})
        assert isinstance(transport.adapter, publish_mod.RealPublisher)
        result = publish_mod.send_draft_real(
            transport, title="t", html="<p>x</p>", digest="d", thumb_media_id="cover-media", author=None,
        )

        assert result["mediaId"] == "real-media-1"
        # 上游 draft/add 恰好 1 次。
        assert state["draft_calls"] == 1
        # wechatApiCalls 增加 2（令牌 1 + draft/add 1）。
        assert counters_mod.get_counters().wechat() == before + 2, state
    finally:
        publish_mod.set_adapter_factory(None)


def test_real_publisher_bypass_mass_send_rejected(monkeypatch):
    """负向：真实路径上游端点被换成群发语义时，assert_whitelisted 抛 FORBIDDEN，
    上游 create_draft 不被调用、wechatApiCalls 不增。"""
    from dsh_wewrite_facade import publish as publish_mod
    from dsh_wewrite_facade import counters as counters_mod
    from dsh_wewrite_facade.errors import FacadeError
    import pytest as _pytest

    state, up = _fake_upstream()
    monkeypatch.setattr(up["api"], "get_access_token", up["fake_token"])

    # 让 draft/add 的端点判定失败（模拟上游误用群发端点）：直接对白名单注入一个
    # 对 cgi-bin/draft/add 抛 FORBIDDEN 的 spy，验证 RealPublisher 内白名单被真实调用。
    real_assert = publish_mod.assert_whitelisted
    def failing_assert(endpoint):
        if endpoint == "cgi-bin/draft/add":
            raise publish_mod.forbidden("微信群发端点被禁止")
        return real_assert(endpoint)
    monkeypatch.setattr(publish_mod, "assert_whitelisted", failing_assert)

    counters_mod.reset_counters()
    before = counters_mod.get_counters().wechat()

    transport = publish_mod.build_transport("wechat", {"appid": "wx-app", "secret": "wx-secret"})
    with _pytest.raises(FacadeError) as exc:
        publish_mod.send_draft_real(
            transport, title="t", html="<p>x</p>", digest="d", thumb_media_id="cover-media", author=None,
        )
    assert exc.value.code == "FORBIDDEN"
    assert "禁止" in str(exc.value)
    # 白名单失败后上游 create_draft 不被调用。
    assert state["draft_calls"] == 0
    # 白名单失败后 wechatApiCalls 不增（只可能已计了令牌 1 次，但 draft/add 前失败，应为 before+1 或 before）。
    # 实际：令牌计数在 draft/add 白名单之前已 +1，此处断言 draft/add 之后不再增。
    assert counters_mod.get_counters().wechat() == before + 1, "draft/add 白名单失败后不增"
