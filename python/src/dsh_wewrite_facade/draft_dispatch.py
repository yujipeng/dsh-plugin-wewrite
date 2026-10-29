"""draft 推送统一 Module（设计修订版二 §1/§2/§4/§5）。

Interface：prepare(payload) / send(payload, context) / status(runId, operationId) / recover(payload)。
RPC 和工具共享本 Module；`draft.recover` 只在 host 私有 facade bridge 使用，不进浏览器 RPC。

状态机：prepared → claimed → dispatching → sent|unknown；prepared/claimed 可转 revoked。
发送段必须在 send 门闩内（本轮唯一不可妥协点）。
"""

from __future__ import annotations

import json
import secrets
import time
import uuid
from typing import Any

from . import binding, counters, intents, paths, projection, publish, storage
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


def prepare(payload: dict) -> dict[str, Any]:
    """本地预检，不取令牌/不上传。run 短锁内读 binding/正文/门禁，写 prepared。"""
    run_id = payload["runId"]
    expected = payload["expectedVersion"]
    content, version = projection.read_artifact_content(run_id, "article")
    if expected is not None and expected != version:
        raise version_conflict("正文版本已变化，请刷新后重试")
    gate = projection.build_gate({}, run_id)

    current_binding = binding.read_binding(run_id)
    workflow_id = current_binding.get("workflowId", "") if isinstance(current_binding, dict) else ""
    generation = current_binding.get("generation", 1) if isinstance(current_binding, dict) else 1
    if not workflow_id:
        raise forbidden("run 未绑定写作会话，无法预检推送")
    intent_id = uuid.uuid4().hex
    expires_at = ""  # S3 无真实推送，默认不过期（S4 补 TTL）。

    with storage.run_lock(run_id, create=False):
        storage.ensure_writable_state(run_id)
        intents.record_intent(
            run_id,
            intent_id=intent_id,
            workflow_id=workflow_id,
            generation=generation,
            article_version=version,
            expires_at=expires_at,
        )

    summary = {
        "runId": run_id,
        "title": "",
        "digest": "",
        "articleVersion": version,
        "themeId": payload["themeId"],
        "coverHash": "",
        "contentHash": version,
        "accountLabel": "",
        "dryRun": True,
    }
    return business_success({
        "intentId": intent_id,
        "expiresAt": expires_at,
        "gate": gate or {"publishable": False, "issues": [], "articleVersion": version, "reportVersion": None},
        "summary": summary,
    })


def send(payload: dict, context: dict[str, Any]) -> dict[str, Any]:
    """全链发送（状态机 + send 门闩）。context 来自 host 可信绑定。

    1. run 短锁 CAS prepared→claimed，写 operationId/claimNonce。
    2. 出锁本地构造 transport/参数。
    3. 取 send 门闩 → run 短锁二次复验 → CAS claimed→dispatching + fsync。
    4. 持门闩发送。
    5. 持门闩重取 run 锁提交 sent/unknown。
    """
    run_id = payload["runId"]
    intent_id = payload["intentId"]

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
            # 已 claim 但尚未 dispatching：返回既有 queued 操作（门闩忙语义由外层处理）。
            if item.get("operationId"):
                return business_success({
                    "operationId": item["operationId"],
                    "status": "pending_approval",
                    "mediaId": None,
                    "dryRun": True,
                })
        # 复验 context 与 binding/intent 一致
        if not _verify_context(run_id, item, context):
            raise cancelled("推送意图已失效，需重新预检", run_id=run_id)
        intents.transition_intent(run_id, intent_id, expected_state="prepared", new_state="claimed",
                                  extra={"operationId": operation_id, "claimNonce": claim_nonce,
                                         "claimedAt": _now(), "state": "claimed"})

    # 2) 本地构造（无网络）
    try:
        content, version = projection.read_artifact_content(run_id, "article")
        transport = publish.build_transport("fake")
    except FacadeError:
        # 构造失败终结 claimed → revoked
        with storage.run_lock(run_id, create=False):
            intents.transition_intent(run_id, intent_id, expected_state="claimed", new_state="revoked",
                                      extra={"issue": "本地构造失败"})
        raise

    # 3) 取 send 门闩 → 二次复验 → dispatching
    try:
        with storage.send_lock(run_id, create=True):
            with storage.run_lock(run_id, create=False):
                item = intents.get_intent(run_id, intent_id)
                if not isinstance(item, dict):
                    raise not_found("推送意图不存在")
                if item.get("claimNonce") != claim_nonce or item.get("state") != "claimed":
                    raise cancelled("推送意图已被接管撤销", run_id=run_id)
                # 二次复验
                if not _verify_context(run_id, item, context):
                    raise cancelled("推送意图已失效，需重新预检", run_id=run_id)
                if payload["expectedVersion"] is not None and payload["expectedVersion"] != version:
                    raise version_conflict("正文版本已变化，请刷新后重试")
                intents.transition_intent(run_id, intent_id, expected_state="claimed", new_state="dispatching",
                                          extra={"dispatchStartedAt": _now(), "state": "dispatching"})

            # 4) 持门闩发送（本轮唯一不可妥协点）
            try:
                result = publish.send_draft_fake(
                    transport,
                    title="",
                    html=content,
                    digest="",
                    thumb_media_id=None,
                    author=None,
                )
                outcome = "sent"
                extra = {"result": {"mediaId": result["mediaId"], "finalHtmlHash": None},
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
            # 门闩忙：保持 claimed，返回 RUN_BUSY（由 host 在 child close 后 recover）。
            raise
        raise

    if outcome == "sent":
        return business_success({
            "operationId": operation_id,
            "status": "succeeded",
            "mediaId": result["mediaId"],
            "dryRun": True,
            "counters": counters.get_counters().snapshot(),
        })
    return business_success({
        "operationId": operation_id,
        "status": "unknown",
        "mediaId": None,
        "dryRun": True,
        "counters": counters.get_counters().snapshot(),
    })


def status(run_id: str, operation_id: str) -> dict[str, Any]:
    """只读投影：按 operationId 找 intent，返回对应状态。dispatching 一律 sending。"""
    for item in intents.read_intents(run_id):
        if not isinstance(item, dict):
            continue
        if item.get("operationId") != operation_id:
            continue
        state = item.get("state")
        if state == "sent":
            return business_success({
                "operationId": operation_id,
                "status": "succeeded",
                "mediaId": (item.get("result") or {}).get("mediaId"),
                "issue": None,
                "dryRun": True,
            })
        if state == "unknown":
            return business_success({
                "operationId": operation_id,
                "status": "unknown",
                "mediaId": (item.get("result") or {}).get("mediaId"),
                "issue": None,
                "dryRun": True,
            })
        if state == "revoked":
            return business_success({
                "operationId": operation_id,
                "status": "failed",
                "mediaId": None,
                "issue": {"code": "CANCELLED", "message": "推送意图已被撤销", "blocking": True},
                "dryRun": True,
            })
        # prepared/claimed/dispatching → 一律 sending（只读侧不推断活性）
        return business_success({
            "operationId": operation_id,
            "status": "sending",
            "mediaId": None,
            "issue": None,
            "dryRun": True,
        })
    raise not_found("推送操作不存在")


def recover(payload: dict) -> dict[str, Any]:
    """显式恢复（私有 facade operation）：claimed→revoked、dispatching→unknown、终态不动。

    锁序：send 门闩 → run 锁。全程不取 home、不调用 transport。幂等：重复恢复零变更不重写。
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
            for item in items:
                if not isinstance(item, dict):
                    continue
                if intent_id is not None and item.get("intentId") != intent_id:
                    continue
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
    """二次复验 context 与 binding/intent 的 runId/workflowId/generation 一致。"""
    if context.get("runId") != run_id:
        return False
    if item.get("workflowId") != context.get("workflowId"):
        return False
    if item.get("generation") != context.get("generation"):
        return False
    current_binding = binding.read_binding(run_id)
    if isinstance(current_binding, dict):
        if current_binding.get("workflowId") != context.get("workflowId"):
            return False
        if current_binding.get("generation") != context.get("generation"):
            return False
        revoked = current_binding.get("revokedIntentIds")
        if isinstance(revoked, list) and item.get("intentId") in revoked:
            return False
    return True


def _existing_operation(run_id: str, item: dict[str, Any]) -> dict[str, Any]:
    return business_success({
        "operationId": item.get("operationId"),
        "status": "sending",
        "mediaId": None,
        "dryRun": True,
    })
