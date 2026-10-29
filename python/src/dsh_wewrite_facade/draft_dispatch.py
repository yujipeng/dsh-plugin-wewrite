"""draft 推送统一 Module（设计修订版二 + S4 双闸）。

Interface：prepare(payload) / send(payload, context) / status(runId, operationId) / recover(payload)。
RPC 和工具共享本 Module；`draft.recover` 只在 host 私有 facade bridge 使用，不进浏览器 RPC。

状态机：prepared → claimed → dispatching → sent|unknown；prepared/claimed 可转 revoked。
发送段必须在 send 门闩内（本轮唯一不可妥协点）。

S4 收紧：
- prepare 是完整本地预检：编辑决策 pass + 同版本 publishable 报告 + 正文/摘要/图片/表格限制
  + 实际封面可读 + 主题可用 + 无路径越界。不取令牌、不上传、不做网络诊断。
- send 二次复验是业务状态复验（第二闸）：armed/pushEnabled 在 host 侧把关；这里复验
  binding/generation、intent 未过期未消费、正文版本、封面/主题/账号哈希、同版本
  publishable、非空正文与内容限制、实际封面、主题可用，以及编辑元数据（title/digest）。
- 发布白名单在 publish.assert_whitelisted：仅令牌/正文图片上传/封面素材上传/draft.add/
  draft.get，无 freepublish/mass/send 群发端点。
- intentId 独立幂等：journal 唯一键只允许一次 draft/add；unknown 墓碑不释放。
- 推送日志：外部调用前 state=dispatching（sending），成功记 mediaId 与最终 HTML 哈希；
  异常标 unknown，不自动重试 add。
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
import uuid
from typing import Any

from . import assets, binding, counters, intents, paths, precheck, projection, publish, storage
from .errors import (
    FacadeError,
    business_success,
    cancelled,
    forbidden,
    invalid_input,
    not_found,
    run_busy,
    state_corrupted,
    version_conflict,
)


def _now() -> int:
    return int(time.time())


# 摘要字段簇唯一权威在 intents.SUMMARY_FIELDS（JEE-1956 整理项）：
# 生产方 _summary_baseline 与复验方 _verify_summary 都按该清单构造/遍历，不再逐字段散落。


def _editorial_meta(run_id: str) -> tuple[str, str]:
    """读 state.yaml editorial.title/digest；缺省回退 H1 标题 / 正文前 120 字节摘要。"""
    import yaml

    title = ""
    digest = ""
    try:
        fd, _st = paths.open_state(run_id)
        with os.fdopen(fd, "rb") as f:
            state = yaml.safe_load(f.read())
    except Exception:
        state = None
    if isinstance(state, dict):
        editorial = state.get("editorial")
        if isinstance(editorial, dict):
            t = editorial.get("title")
            d = editorial.get("digest")
            title = t if isinstance(t, str) else ""
            digest = d if isinstance(d, str) else ""

    if not title:
        try:
            content, _version = projection.read_artifact_content(run_id, "article")
            for line in content.splitlines():
                stripped = line.strip()
                if stripped.startswith("# ") and not stripped.startswith("## "):
                    title = stripped[2:].strip()
                    break
        except FacadeError:
            pass
    if not digest:
        try:
            content, _version = projection.read_artifact_content(run_id, "article")
            # 摘要 ≤ 120 字节：取前 120 字节（UTF-8 边界截断）。
            raw = content.encode("utf-8")[:120]
            digest = raw.decode("utf-8", errors="ignore").rstrip()
        except FacadeError:
            pass
    return title, digest


def _summary_baseline(
    *,
    run_id: str,
    theme_id: str,
    cover_asset_id: str,
    gate: dict[str, Any] | None,
) -> dict[str, Any]:
    """从磁盘真实状态固定推送摘要基线（缺口 1）。

    封面哈希从资产登记表回读（而非空串）；账号 ref/version 在 S4 无微信凭据基线，固定为空。
    返回 dict 的键集与顺序严格等于 intents.SUMMARY_FIELDS。
    """
    report_version = None
    try:
        _, rv = projection.read_artifact_content(run_id, "review_report")
        report_version = rv
    except FacadeError:
        report_version = None
    publishable = bool(gate.get("publishable")) if isinstance(gate, dict) else None

    values = {
        "reportVersion": report_version,
        "publishable": publishable,
        "coverHash": assets.asset_hash(run_id, cover_asset_id),
        # 权威主题取已持久化的 editorial.theme；未保存过时回退 prepare 入参 themeId。
        "themeId": _current_theme_id(run_id) or theme_id,
        "accountRef": "",
        "accountVersion": None,
    }
    return {field: values[field] for field in intents.SUMMARY_FIELDS}


def _verify_summary(run_id: str, item: dict[str, Any], current_gate: dict[str, Any] | None) -> bool:
    """二次复验摘要基线逐项比对（缺口 1，fail-closed）。

    按 intents.SUMMARY_FIELDS 逐字段比对：当前盘上重新派生的基线任一字段与
    intent 固定值不符即拒。accountRef/accountVersion 因 S4 固定为空，天然随该
    比对一起校验（非默认值即拒）。
    """
    current = _summary_baseline(
        run_id=run_id,
        theme_id=item.get("themeId") or "",
        cover_asset_id=item.get("coverAssetId") or "",
        gate=current_gate,
    )
    for field in intents.SUMMARY_FIELDS:
        if item.get(field) != current[field]:
            return False
    return True


def _current_theme_id(run_id: str) -> str:
    """读 state.yaml editorial.theme（与 article.save 落盘口径一致）；读不到返回空串。"""
    import yaml

    try:
        fd, _st = paths.open_state(run_id)
    except FacadeError:
        return ""
    try:
        with os.fdopen(fd, "rb") as f:
            state = yaml.safe_load(f.read())
    except Exception:
        return ""
    if not isinstance(state, dict):
        return ""
    editorial = state.get("editorial")
    theme = editorial.get("theme") if isinstance(editorial, dict) else None
    return theme if isinstance(theme, str) else ""


def _theme_usable(theme_id: str) -> bool:
    """主题可用：从已核实主题目录枚举（无路径越界，只认目录内枚举名）。"""
    for item in projection.list_theme_ids():
        if isinstance(item, dict) and item.get("id") == theme_id:
            return True
    return False


def _verify_content_fixed(run_id: str, item: dict[str, Any]) -> tuple[bool, str]:
    """第二闸内容固定复验：正文内容限制 + 标题/摘要与 intent 固定值一致 + 实际封面可读。

    返回 (通过?, 拒绝原因)。
    """
    content, _version = projection.read_artifact_content(run_id, "article")
    title, digest = _editorial_meta(run_id)
    issues = precheck.content_issues(markdown=content, title=title, digest=digest)
    if issues:
        return False, issues[0]["message"]
    if item.get("title") != title:
        return False, "草稿标题已变化，需重新预检"
    if item.get("digest") != digest:
        return False, "草稿摘要已变化，需重新预检"
    # 实际封面可读：assetId 查登记项 + 复核真实路径/哈希（设计 §7）。
    cover_asset_id = item.get("coverAssetId") or ""
    try:
        assets.get_asset(run_id, cover_asset_id)
    except FacadeError as exc:
        return False, str(exc) or "封面不可读"
    return True, ""


def prepare(payload: dict) -> dict[str, Any]:
    """完整本地预检（S4）：编辑决策 pass + 同版本 publishable + 内容限制 + 封面/主题可用。

    不取令牌、不上传、不做网络诊断。锁内读 binding/正文/门禁后登记；
    expiresAt 真实服务端时间（默认五分钟）。
    """
    run_id = payload["runId"]
    expected = payload["expectedVersion"]
    cover_asset_id = payload["coverAssetId"]
    theme_id = payload["themeId"]
    intent_id = uuid.uuid4().hex
    expires_at = int(time.time()) + 300

    with storage.run_lock(run_id, create=False):
        storage.ensure_writable_state(run_id)
        content, version = projection.read_artifact_content(run_id, "article")
        if expected is not None and expected != version:
            raise version_conflict("正文版本已变化，请刷新后重试")
        gate = projection.build_gate({}, run_id)
        # S4：编辑决策必须 pass 且同版本 publishable，否则拒（不允许「能发」）。
        if not isinstance(gate, dict) or gate.get("publishable") is not True:
            raise FacadeError("REVIEW_REQUIRED", "需要先完成审稿且通过", retryable=False)
        current_binding = binding.read_binding(run_id)
        if not isinstance(current_binding, dict):
            raise forbidden("run 未绑定写作会话，无法预检推送")
        if binding.is_cancelled_phase(current_binding):
            raise forbidden("run 已取消，无法预检推送")
        workflow_id = current_binding.get("workflowId", "")
        generation = current_binding.get("generation", 1)
        if not workflow_id:
            raise forbidden("run 未绑定写作会话，无法预检推送")

        # 内容限制（正文/摘要/图片/表格 + 非空标题）。
        title, digest = _editorial_meta(run_id)
        issues = precheck.content_issues(markdown=content, title=title, digest=digest)
        if issues:
            raise invalid_input(issues[0]["message"])
        # 实际封面可读（assetId 查登记 + 真实路径/哈希复核）。
        try:
            assets.get_asset(run_id, cover_asset_id)
        except FacadeError as exc:
            raise FacadeError("ARTIFACT_MISSING", "封面缺失或不可读", retryable=False) from exc
        # 主题可用（从已核实主题目录枚举）。
        if not _theme_usable(theme_id):
            raise invalid_input("主题不可用")

        # 摘要字段基线从磁盘真实状态固定。
        summary = _summary_baseline(
            run_id=run_id,
            theme_id=theme_id,
            cover_asset_id=cover_asset_id,
            gate=gate,
        )
        intents.record_intent(
            run_id,
            intent_id=intent_id,
            workflow_id=workflow_id,
            generation=generation,
            article_version=version,
            expires_at=str(expires_at),
            report_version=summary["reportVersion"],
            publishable=summary["publishable"],
            cover_hash=summary["coverHash"],
            theme_id=summary["themeId"],
            account_ref=summary["accountRef"],
            account_version=summary["accountVersion"],
            cover_asset_id=cover_asset_id,
            title=title,
            digest=digest,
        )

    summary_out = {
        "runId": run_id,
        "title": title,
        "digest": digest,
        "articleVersion": version,
        "themeId": summary["themeId"],
        "coverHash": summary["coverHash"],
        "contentHash": version,
        "accountLabel": "",
        "dryRun": True,
    }
    return business_success({
        "intentId": intent_id,
        "expiresAt": str(expires_at),
        "gate": gate or {"publishable": False, "issues": [], "articleVersion": version, "reportVersion": None},
        "summary": summary_out,
    })


def send(payload: dict, context: dict[str, Any]) -> dict[str, Any]:
    """全链发送（状态机 + send 门闩 + S4 第二闸）。context 来自 host 可信绑定。

    1. run 短锁 CAS prepared→claimed，写 operationId/claimNonce/transport。
    2. 出锁本地构造 transport/参数。
    3. 取 send 门闩 → run 短锁二次复验（context/binding/intent/正文版本/门禁/摘要/内容全一致）
       → CAS claimed→dispatching + fsync。
    4. 持门闩发送（真实或假 transport，白名单 + 计数器）。
    5. 持门闩重取 run 锁提交 sent/unknown。
    """
    run_id = payload["runId"]
    intent_id = payload["intentId"]
    transport_kind = payload.get("transport", "fake")
    credentials = payload.get("credentials")

    # 1) claim（run 短锁）
    claim_nonce = secrets.token_hex(8)
    operation_id = uuid.uuid4().hex
    with storage.run_lock(run_id, create=False):
        storage.ensure_writable_state(run_id)
        item = intents.get_intent(run_id, intent_id)
        if not isinstance(item, dict):
            raise not_found("推送意图不存在")
        state = item.get("state")
        if state in ("sent", "unknown", "revoked"):
            raise FacadeError("INVALID_TRANSITION", f"推送记录已是终态 {state}")
        if state == "dispatching":
            return _existing_operation(run_id, item)
        if state == "claimed":
            if item.get("operationId"):
                return business_success({
                    "operationId": item["operationId"],
                    "status": "pending_approval",
                    "mediaId": None,
                    "dryRun": item.get("dryRun", True),
                })
        # 复验 context 与 binding/intent 一致
        if not _verify_context(run_id, item, context):
            raise cancelled("推送意图已失效，需重新预检", run_id=run_id)
        intents.transition_intent(run_id, intent_id, expected_state="prepared", new_state="claimed",
                                  extra={"operationId": operation_id, "claimNonce": claim_nonce,
                                         "claimedAt": _now(), "state": "claimed",
                                         "transport": transport_kind,
                                         "dryRun": transport_kind != "wechat"})

    # 2) 本地构造 transport（无网络；正文读取在门闩→run锁内当场重读，不在此处读）。
    try:
        transport = publish.build_transport(transport_kind, credentials)
    except FacadeError:
        with storage.run_lock(run_id, create=False):
            intents.transition_intent(run_id, intent_id, expected_state="claimed", new_state="revoked",
                                      extra={"issue": "本地构造失败"})
        raise

    # 3) 取 send 门闩 → 门闩内当场重读快照 + 二次复验 → dispatching
    try:
        with storage.send_lock(run_id, create=True):
            with storage.run_lock(run_id, create=False):
                item = intents.get_intent(run_id, intent_id)
                if not isinstance(item, dict):
                    raise not_found("推送意图不存在")
                if item.get("claimNonce") != claim_nonce or item.get("state") != "claimed":
                    raise cancelled("推送意图已被接管撤销", run_id=run_id)
                # 二次复验（context/binding）
                if not _verify_context(run_id, item, context):
                    raise cancelled("推送意图已失效，需重新预检", run_id=run_id)
                # 当场重读正文/报告/门禁/摘要，逐项比对 intent 固定值。
                content, version = projection.read_artifact_content(run_id, "article")
                if payload["expectedVersion"] is not None and payload["expectedVersion"] != version:
                    raise version_conflict("正文版本已变化，请刷新后重试")
                if item.get("articleVersion") is not None and item.get("articleVersion") != version:
                    raise version_conflict("正文版本已变化，请刷新后重试")
                current_gate = projection.build_gate({}, run_id)
                if not _verify_summary(run_id, item, current_gate):
                    raise cancelled("推送摘要已变化，需重新预检", run_id=run_id)
                # S4 第二闸：内容限制 + title/digest + 实际封面可读。
                ok, reason = _verify_content_fixed(run_id, item)
                if not ok:
                    raise cancelled(reason, run_id=run_id)
                intents.transition_intent(run_id, intent_id, expected_state="claimed", new_state="dispatching",
                                          extra={"dispatchStartedAt": _now(), "state": "dispatching"})

            # 4) 持门闩发送（使用当场固定的快照，绝不使用门闩外读到的 content/version）
            try:
                final_html = content
                if transport.is_fake:
                    result = publish.send_draft_fake(
                        transport,
                        title=item.get("title", ""),
                        html=final_html,
                        digest=item.get("digest", ""),
                        thumb_media_id=None,
                        author=None,
                    )
                else:
                    # 真实发送：封面 thumb_media_id 由封面资产上传产生；S4 上传封面素材
                    # 走白名单 + 计数。这里从资产登记读本地文件路径，经 facade 上传。
                    thumb_media_id = _upload_cover_media(transport, run_id, item)
                    result = publish.send_draft_real(
                        transport,
                        title=item.get("title", ""),
                        html=final_html,
                        digest=item.get("digest", ""),
                        thumb_media_id=thumb_media_id,
                        author=None,
                    )
                final_html_hash = hashlib.sha256(final_html.encode("utf-8")).hexdigest()
                outcome = "sent"
                extra = {"result": {"mediaId": result["mediaId"], "finalHtmlHash": final_html_hash,
                                    "dryRun": transport.is_fake},
                         "state": "sent"}
            except Exception:
                outcome = "unknown"
                extra = {"state": "unknown", "issue": "发送结果无法确定"}

            # 5) 持门闩重取 run 锁提交
            with storage.run_lock(run_id, create=False):
                intents.transition_intent(run_id, intent_id, expected_state="dispatching", new_state=outcome,
                                          extra=extra)
    except FacadeError as exc:
        if exc.code == "RUN_BUSY":
            raise
        raise

    if outcome == "sent":
        return business_success({
            "operationId": operation_id,
            "status": "succeeded",
            "mediaId": result["mediaId"],
            "dryRun": transport.is_fake,
            "counters": counters.get_counters().snapshot(),
        })
    return business_success({
        "operationId": operation_id,
        "status": "unknown",
        "mediaId": None,
        "dryRun": transport.is_fake,
        "counters": counters.get_counters().snapshot(),
    })


def _upload_cover_media(transport: publish.PublishTransport, run_id: str, item: dict[str, Any]) -> str | None:
    """真实发送封面素材上传（白名单 + 计数）。

    封面必须已登记为本地资产；先取令牌（白名单 + 计数），再经 wechat_api.upload_thumb
    上传（白名单 + 计数），返回 mediaId。失败抛 PUBLISH_UNKNOWN（不自动重试）。
    """
    from wewrite.toolkit.wechat_api import get_access_token, upload_thumb

    cover_asset_id = item.get("coverAssetId") or ""
    entry = assets.get_asset(run_id, cover_asset_id)

    # 1) 令牌（白名单 + 计数）。
    publish.assert_whitelisted("cgi-bin/token")
    counters.get_counters().inc_wechat()
    try:
        token = get_access_token(transport.adapter.appid, transport.adapter.secret)
    except Exception:
        raise FacadeError("PUBLISH_UNKNOWN", "微信令牌获取失败，结果无法确定", retryable=False)

    # 2) 封面素材上传（白名单 + 计数）。
    publish.assert_whitelisted("cgi-bin/material/add_material")
    counters.get_counters().inc_wechat()
    try:
        import base64
        import tempfile
        data = base64.b64decode(entry["base64"])
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(data)
            tmp_path = f.name
        try:
            return upload_thumb(token, tmp_path)
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    except FacadeError:
        raise
    except Exception:
        raise FacadeError("PUBLISH_UNKNOWN", "封面上传结果无法确定", retryable=False)


def status(run_id: str, operation_id: str) -> dict[str, Any]:
    """只读投影：按 operationId 找 intent，返回对应状态。dispatching 一律 sending。

    S4：operationId 与 intentId 都作为唯一定位键（intent 是推送操作的唯一权威键，
    主机 pushDraft 用 intentId 作为 operationId，facade send 内部生成的 operationId 也命中）。
    """
    for item in intents.read_intents(run_id):
        if not isinstance(item, dict):
            continue
        if item.get("operationId") != operation_id and item.get("intentId") != operation_id:
            continue
        dry = item.get("dryRun", True)
        state = item.get("state")
        if state == "sent":
            return business_success({
                "operationId": operation_id,
                "status": "succeeded",
                "mediaId": (item.get("result") or {}).get("mediaId"),
                "issue": None,
                "dryRun": bool(dry),
            })
        if state == "unknown":
            return business_success({
                "operationId": operation_id,
                "status": "unknown",
                "mediaId": (item.get("result") or {}).get("mediaId"),
                "issue": None,
                "dryRun": bool(dry),
            })
        if state == "revoked":
            return business_success({
                "operationId": operation_id,
                "status": "failed",
                "mediaId": None,
                "issue": {"code": "CANCELLED", "message": "推送意图已被撤销", "blocking": True},
                "dryRun": bool(dry),
            })
        # prepared/claimed/dispatching → 一律 sending（只读侧不推断活性）
        return business_success({
            "operationId": operation_id,
            "status": "sending",
            "mediaId": None,
            "issue": None,
            "dryRun": bool(dry),
        })
    raise not_found("推送操作不存在")


def recover(payload: dict) -> dict[str, Any]:
    """显式恢复（私有 facade operation）：claimed→revoked、dispatching→unknown、终态不动。

    锁序：send 门闩 → run 锁。全程不取 home、不调用 transport。幂等：重复恢复零变更不重写。
    旧格式（schemaVersion<2 或缺 state）整体迁 v2，旧条目保守 unknown；定向 intentId 不存在返回 NOT_FOUND。
    """
    run_id = payload["runId"]
    intent_id = payload.get("intentId")
    examined = 0
    revoked = 0
    unknown = 0
    migrated = 0

    with storage.send_lock(run_id, create=True):
        with storage.run_lock(run_id, create=False):
            items = intents.read_intents(run_id)
            changed = False
            found_target = False

            for item in items:
                if not isinstance(item, dict):
                    continue
                if item.get("state") not in intents.STATES:
                    item["state"] = "unknown"
                    item["issue"] = "旧格式记录迁移，结果无法确定"
                    migrated += 1
                    unknown += 1
                    changed = True

            for item in items:
                if not isinstance(item, dict):
                    continue
                if intent_id is not None and item.get("intentId") != intent_id:
                    continue
                found_target = True
                examined += 1
                state = item.get("state")
                if state == "claimed":
                    item["state"] = "revoked"
                    item["issue"] = "未发送占用已终止"
                    revoked += 1
                    changed = True
                elif state == "dispatching":
                    item["state"] = "unknown"
                    item["issue"] = "发送结果无法确定"
                    unknown += 1
                    changed = True
                # sent/unknown/revoked/prepared：不动

            if intent_id is not None and not found_target:
                raise not_found("推送意图不存在")
            if changed:
                intents._write_intents(run_id, items)

    return business_success({
        "runId": run_id,
        "examined": examined,
        "revoked": revoked,
        "unknown": unknown,
        "migrated": migrated,
    })


def _verify_context(run_id: str, item: dict[str, Any], context: dict[str, Any]) -> bool:
    """二次复验 context 与 binding/intent 的 runId/workflowId/generation 一致（fail-closed）。

    binding 不存在/不可读时返回 False，不得跳过核对。
    """
    if context.get("runId") != run_id:
        return False
    if item.get("workflowId") != context.get("workflowId"):
        return False
    if item.get("generation") != context.get("generation"):
        return False
    current_binding = binding.read_binding(run_id)
    if not isinstance(current_binding, dict):
        return False
    if current_binding.get("workflowId") != context.get("workflowId"):
        return False
    if current_binding.get("generation") != context.get("generation"):
        return False
    if binding.is_cancelled_phase(current_binding):
        return False
    revoked = current_binding.get("revokedIntentIds")
    if isinstance(revoked, list) and item.get("intentId") in revoked:
        return False
    return _verify_ttl(item)


def _verify_ttl(item: dict[str, Any]) -> bool:
    """TTL 校验（独立 seam，供返修真红自证用 monkeypatch 回退）。"""
    expires = item.get("expiresAt")
    if not isinstance(expires, str) or not expires:
        return False
    try:
        if int(expires) <= int(time.time()):
            return False
    except ValueError:
        return False
    return True


def _existing_operation(run_id: str, item: dict[str, Any]) -> dict[str, Any]:
    return business_success({
        "operationId": item.get("operationId"),
        "status": "sending",
        "mediaId": None,
        "dryRun": bool(item.get("dryRun", True)),
    })
