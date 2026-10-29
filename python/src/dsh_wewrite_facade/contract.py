"""请求 schema 校验（与 src/shared/contract.ts 等价，Python 侧）。

只做形状校验，返回中文错误信息。operation 白名单固定；不接受 Python 模块名、
任意 argv、文件路径或 shell。
"""

from __future__ import annotations

from typing import Any

from .errors import invalid_input

PROTOCOL = 1

# operation 白名单（设计 §6）。
OPERATIONS = (
    "diagnose",
    "snapshot",
    "run.list",
    "hotspots.fetch",
    "run.create",
    "run.detail",
    "run.step",
    "run.finish",
    "run.fork",
    "artifact.get",
    "artifact.write",
    "article.save",
    "review.build",
    "preview.render",
    "asset.put",
    "asset.get",
    "binding.publish",
    "binding.get",
    "binding.revoke-intents",
    "draft.prepare",
    "draft.send",
    "draft.status",
    "draft.recover",
)

KINDS = ("brief", "claims", "draft", "article", "review_report", "sources",
         "illustrated_article", "image_prompts", "images_manifest")
MODES = ("draft", "complete")
VISUAL_MODES = ("none", "cover", "full", "prompts")
STEPS = ("topic", "write", "review", "visual", "publish")
STEP_STATUSES = ("pending", "in_progress", "completed", "failed", "skipped")
ASSET_ROLES = ("cover", "inline")

RUN_ID_RE = r"^[0-9]{8}-[0-9]{6}-[a-f0-9]{6}$"
HASH_RE = r"^[a-f0-9]{64}$"
ID_RE = r"^\S+$"

TOPIC_MAX = 500
TEXT_MAX_BYTES = 2 * 1024 * 1024
IMAGE_MAX_BYTES = 10 * 1024 * 1024
HOTSPOT_LIMIT_MIN = 1
HOTSPOT_LIMIT_MAX = 50
PAGE_LIMIT_MIN = 1
PAGE_LIMIT_MAX = 100

import re  # noqa: E402


def _require_str(value: Any, field: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value):
        raise invalid_input(f"{field} 不合法")
    return value


def _require_run_id(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(RUN_ID_RE, value):
        raise invalid_input("runId 不合法")
    return value


def _require_kind(value: Any) -> str:
    if value not in KINDS:
        raise invalid_input("kind 不合法")
    return value


def _require_request_id(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise invalid_input("requestId 不合法")
    return value


def _optional_binding_context(p: dict, run_id: str) -> dict | None:
    """可选 fencing 上下文（设计 §5）：Agent 工具写路径必须携带，RPC owner 编辑路径不带。

    携带时校验 runId 一致，返回 {"runId","workflowId","generation"}；不携带返回 None。
    """
    ctx = p.get("context")
    if ctx is None:
        return None
    if not isinstance(ctx, dict):
        raise invalid_input("context 不合法")
    ctx_run = _require_run_id(ctx.get("runId"))
    if ctx_run != run_id:
        raise invalid_input("context.runId 与 runId 不一致")
    workflow_id = _require_str(ctx.get("workflowId"), "context.workflowId")
    generation = ctx.get("generation")
    if isinstance(generation, bool) or not isinstance(generation, int) or generation < 1:
        raise invalid_input("context.generation 不合法")
    return {"runId": run_id, "workflowId": workflow_id, "generation": generation}


def _require_optional_version(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(HASH_RE, value):
        raise invalid_input("expectedVersion 不合法")
    return value


def _require_bytes_limit(value: str, field: str, limit: int) -> str:
    if not isinstance(value, str):
        raise invalid_input(f"{field} 不合法")
    if len(value.encode("utf-8")) > limit:
        raise invalid_input(f"{field} 超过 {limit} 字节（UTF-8）")
    return value


def _base64_decoded_bytes(value: str) -> int:
    clean = value.replace("\r", "").replace("\n", "").replace("\t", "").replace(" ", "")
    padding = 2 if clean.endswith("==") else 1 if clean.endswith("=") else 0
    return (len(clean) * 3) // 4 - padding


def validate_operation(operation: str) -> str:
    if operation not in OPERATIONS:
        raise invalid_input("未知操作")
    return operation


def validate_payload(operation: str, payload: Any) -> dict:
    """校验 payload 并返回归一化 dict（中文错误，不含栈/秘密）。"""
    if not isinstance(payload, dict):
        raise invalid_input("payload 不合法")
    fn = _VALIDATORS.get(operation, lambda p: p)
    return fn(payload)


def _v_diagnose(p: dict) -> dict:
    return {}


def _v_snapshot(p: dict) -> dict:
    return {}


def _v_run_list(p: dict) -> dict:
    cursor = p.get("cursor")
    limit = p.get("limit")
    if cursor is not None and (not isinstance(cursor, str) or not cursor.strip()):
        raise invalid_input("cursor 不合法")
    if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int)
                              or not (PAGE_LIMIT_MIN <= limit <= PAGE_LIMIT_MAX)):
        raise invalid_input(f"limit 须在 {PAGE_LIMIT_MIN}–{PAGE_LIMIT_MAX}")
    return {"cursor": cursor, "limit": limit}


def _v_hotspots(p: dict) -> dict:
    limit = p.get("limit")
    if not isinstance(limit, int) or isinstance(limit, bool) or not (HOTSPOT_LIMIT_MIN <= limit <= HOTSPOT_LIMIT_MAX):
        raise invalid_input(f"limit 须在 {HOTSPOT_LIMIT_MIN}–{HOTSPOT_LIMIT_MAX}")
    return {"limit": limit}


def _v_run_create(p: dict) -> dict:
    request_id = _require_request_id(p.get("requestId"))
    topic = _require_str(p.get("topic"), "topic", allow_empty=True)
    if len(topic) > TOPIC_MAX:
        raise invalid_input(f"主题不能超过 {TOPIC_MAX} 字")
    mode = p.get("mode")
    if mode not in MODES:
        raise invalid_input("mode 不合法")
    visual_mode = p.get("visualMode")
    if visual_mode not in VISUAL_MODES:
        raise invalid_input("visualMode 不合法")
    theme_id = _require_str(p.get("themeId"), "themeId")
    return {"requestId": request_id, "topic": topic, "mode": mode,
            "visualMode": visual_mode, "themeId": theme_id}


def _v_run_detail(p: dict) -> dict:
    return {"runId": _require_run_id(p.get("runId"))}


def _v_run_step(p: dict) -> dict:
    request_id = _require_request_id(p.get("requestId"))
    run_id = _require_run_id(p.get("runId"))
    step = p.get("step")
    if step not in STEPS:
        raise invalid_input("step 不合法")
    status = p.get("status")
    if status not in STEP_STATUSES:
        raise invalid_input("status 不合法")
    error = p.get("error")
    if error is not None and not isinstance(error, str):
        raise invalid_input("error 不合法")
    context = _optional_binding_context(p, run_id)
    return {"requestId": request_id, "runId": run_id, "step": step, "status": status,
            "error": error, "context": context}


def _v_run_finish(p: dict) -> dict:
    request_id = _require_request_id(p.get("requestId"))
    run_id = _require_run_id(p.get("runId"))
    expected_version = _require_optional_version(p.get("expectedVersion"))
    report_version = _require_optional_version(p.get("reportVersion"))
    context = _optional_binding_context(p, run_id)
    return {"requestId": request_id, "runId": run_id, "expectedVersion": expected_version,
            "reportVersion": report_version, "context": context}


def _v_run_fork(p: dict) -> dict:
    request_id = _require_request_id(p.get("requestId"))
    run_id = _require_run_id(p.get("runId"))
    expected_version = _require_optional_version(p.get("expectedVersion"))
    return {"requestId": request_id, "runId": run_id, "expectedVersion": expected_version}


def _v_artifact_get(p: dict) -> dict:
    return {"runId": _require_run_id(p.get("runId")), "kind": _require_kind(p.get("kind"))}


def _v_artifact_write(p: dict) -> dict:
    request_id = _require_request_id(p.get("requestId"))
    run_id = _require_run_id(p.get("runId"))
    kind = _require_kind(p.get("kind"))
    content = p.get("content")
    if not isinstance(content, str):
        raise invalid_input("content 不合法")
    content = _require_bytes_limit(content, "content", TEXT_MAX_BYTES)
    expected_version = _require_optional_version(p.get("expectedVersion"))
    context = _optional_binding_context(p, run_id)
    return {"requestId": request_id, "runId": run_id, "kind": kind,
            "content": content, "expectedVersion": expected_version, "context": context}


def _v_article_save(p: dict) -> dict:
    request_id = _require_request_id(p.get("requestId"))
    run_id = _require_run_id(p.get("runId"))
    markdown = p.get("markdown")
    if not isinstance(markdown, str):
        raise invalid_input("markdown 不合法")
    markdown = _require_bytes_limit(markdown, "markdown", TEXT_MAX_BYTES)
    expected_version = _require_optional_version(p.get("expectedVersion"))
    metadata = p.get("metadata")
    if not isinstance(metadata, dict):
        raise invalid_input("metadata 不合法")
    title = _require_str(metadata.get("title"), "metadata.title")
    digest = _require_str(metadata.get("digest"), "metadata.digest")
    theme_id = _require_str(metadata.get("themeId"), "metadata.themeId")
    return {"requestId": request_id, "runId": run_id, "markdown": markdown,
            "expectedVersion": expected_version,
            "metadata": {"title": title, "digest": digest, "themeId": theme_id}}


def _v_review_build(p: dict) -> dict:
    request_id = _require_request_id(p.get("requestId"))
    run_id = _require_run_id(p.get("runId"))
    article_version = _require_optional_version(p.get("articleVersion"))
    draft_version = _require_optional_version(p.get("draftVersion"))
    assessment = p.get("assessment")
    if not isinstance(assessment, dict):
        raise invalid_input("assessment 不合法")
    context = _optional_binding_context(p, run_id)
    return {"requestId": request_id, "runId": run_id, "articleVersion": article_version,
            "draftVersion": draft_version, "assessment": assessment, "context": context}


def _v_preview_render(p: dict) -> dict:
    request_id = _require_request_id(p.get("requestId"))
    run_id = _require_run_id(p.get("runId"))
    kind = p.get("kind")
    if kind not in ("article", "illustrated_article"):
        raise invalid_input("kind 不合法")
    expected_version = _require_optional_version(p.get("expectedVersion"))
    theme_id = _require_str(p.get("themeId"), "themeId")
    return {"requestId": request_id, "runId": run_id, "kind": kind,
            "expectedVersion": expected_version, "themeId": theme_id}


def _v_asset_put(p: dict) -> dict:
    request_id = _require_request_id(p.get("requestId"))
    run_id = _require_run_id(p.get("runId"))
    role = p.get("role")
    if role not in ASSET_ROLES:
        raise invalid_input("role 不合法")
    media_type = p.get("mediaType")
    if media_type not in ("image/png", "image/jpeg"):
        raise invalid_input("mediaType 不合法")
    base64 = p.get("base64")
    if not isinstance(base64, str) or not base64:
        raise invalid_input("base64 不合法")
    if _base64_decoded_bytes(base64) > IMAGE_MAX_BYTES:
        raise invalid_input(f"图片解码后不能超过 {IMAGE_MAX_BYTES} 字节")
    expected_article_version = _require_optional_version(p.get("expectedArticleVersion"))
    return {"requestId": request_id, "runId": run_id, "role": role, "mediaType": media_type,
            "base64": base64, "expectedArticleVersion": expected_article_version}


def _v_asset_get(p: dict) -> dict:
    run_id = _require_run_id(p.get("runId"))
    asset_id = _require_str(p.get("assetId"), "assetId")
    return {"runId": run_id, "assetId": asset_id}


def _v_binding_publish(p: dict) -> dict:
    request_id = _require_request_id(p.get("requestId"))
    run_id = _require_run_id(p.get("runId"))
    workflow_id = _require_str(p.get("workflowId"), "workflowId")
    generation = p.get("generation")
    if isinstance(generation, bool) or not isinstance(generation, int) or generation < 1:
        raise invalid_input("generation 不合法")
    agent_id = _require_str(p.get("agentId"), "agentId")
    session_id = _require_str(p.get("sessionId"), "sessionId")
    workspace_id = _require_str(p.get("workspaceId"), "workspaceId")
    phase = p.get("phase")
    if phase not in ("created", "starting", "writing", "reviewing", "ready",
                     "failed", "cancelling", "cancelled", "interrupted"):
        raise invalid_input("phase 不合法")
    expected = p.get("expectedGeneration")
    if expected is not None and (isinstance(expected, bool) or not isinstance(expected, int) or expected < 1):
        raise invalid_input("expectedGeneration 不合法")
    return {"requestId": request_id, "runId": run_id, "workflowId": workflow_id,
            "generation": generation, "agentId": agent_id, "sessionId": session_id,
            "workspaceId": workspace_id, "phase": phase, "expectedGeneration": expected}


def _v_binding_get(p: dict) -> dict:
    return {"runId": _require_run_id(p.get("runId"))}


def _v_binding_revoke_intents(p: dict) -> dict:
    return {"runId": _require_run_id(p.get("runId"))}


def _v_draft_prepare(p: dict) -> dict:
    request_id = _require_request_id(p.get("requestId"))
    run_id = _require_run_id(p.get("runId"))
    expected_version = _require_optional_version(p.get("expectedVersion"))
    cover_asset_id = _require_str(p.get("coverAssetId"), "coverAssetId")
    theme_id = _require_str(p.get("themeId"), "themeId")
    return {"requestId": request_id, "runId": run_id, "expectedVersion": expected_version,
            "coverAssetId": cover_asset_id, "themeId": theme_id}


def _v_draft_send(p: dict) -> dict:
    request_id = _require_request_id(p.get("requestId"))
    run_id = _require_run_id(p.get("runId"))
    intent_id = _require_str(p.get("intentId"), "intentId")
    expected_version = _require_optional_version(p.get("expectedVersion"))
    # 裁定 B：context 是内部字段，只存在于内部 draft.send 协议；严格校验 context.runId === runId。
    context = p.get("context")
    if context is None:
        raise invalid_input("context 缺失（内部发送协议必填）")
    if not isinstance(context, dict):
        raise invalid_input("context 不合法")
    ctx_run_id = _require_run_id(context.get("runId"))
    if ctx_run_id != run_id:
        raise invalid_input("context.runId 与 runId 不一致")
    workflow_id = _require_str(context.get("workflowId"), "context.workflowId")
    generation = context.get("generation")
    if isinstance(generation, bool) or not isinstance(generation, int) or generation < 1:
        raise invalid_input("context.generation 不合法")
    return {"requestId": request_id, "runId": run_id, "intentId": intent_id,
            "expectedVersion": expected_version,
            "context": {"runId": run_id, "workflowId": workflow_id, "generation": generation}}


def _v_draft_status(p: dict) -> dict:
    run_id = _require_run_id(p.get("runId"))
    operation_id = _require_str(p.get("operationId"), "operationId")
    return {"runId": run_id, "operationId": operation_id}


def _v_draft_recover(p: dict) -> dict:
    request_id = _require_request_id(p.get("requestId"))
    run_id = _require_run_id(p.get("runId"))
    intent_id = p.get("intentId")
    if intent_id is not None:
        intent_id = _require_str(intent_id, "intentId")
    # 严格拒绝未知字段（设计 §5：不接受 claimNonce/保留列表/caller 自报存活）。
    allowed = {"requestId", "runId", "intentId"}
    unknown = set(p.keys()) - allowed
    if unknown:
        raise invalid_input(f"未知字段: {', '.join(sorted(unknown))}")
    return {"requestId": request_id, "runId": run_id, "intentId": intent_id}


_VALIDATORS = {
    "diagnose": _v_diagnose,
    "snapshot": _v_snapshot,
    "run.list": _v_run_list,
    "hotspots.fetch": _v_hotspots,
    "run.create": _v_run_create,
    "run.detail": _v_run_detail,
    "run.step": _v_run_step,
    "run.finish": _v_run_finish,
    "run.fork": _v_run_fork,
    "artifact.get": _v_artifact_get,
    "artifact.write": _v_artifact_write,
    "article.save": _v_article_save,
    "review.build": _v_review_build,
    "preview.render": _v_preview_render,
    "asset.put": _v_asset_put,
    "asset.get": _v_asset_get,
    "binding.publish": _v_binding_publish,
    "binding.get": _v_binding_get,
    "binding.revoke-intents": _v_binding_revoke_intents,
    "draft.prepare": _v_draft_prepare,
    "draft.send": _v_draft_send,
    "draft.status": _v_draft_status,
    "draft.recover": _v_draft_recover,
}
