"""intent 注册表（设计修订版二 §3）：schemaVersion=2 状态机持久层。

每项含 state / operationId / claimNonce / claimedAt / dispatchStartedAt / result / issue。
状态机：prepared → claimed → dispatching → sent|unknown；prepared/claimed 可转 revoked。
sent/unknown/revoked 不回 prepared，unknown 墓碑不释放。
binding.json 仍是代次与撤销唯一权威；本文件是消费/结果与 intentId 唯一性的唯一权威。
"""

from __future__ import annotations

import json
import time
from typing import Any

from . import paths, storage
from .errors import FacadeError, forbidden, not_found, state_corrupted

_INTENTS_REL = ".dsh-workbench/intents.json"
SCHEMA_VERSION = 2

STATES = ("prepared", "claimed", "dispatching", "sent", "unknown", "revoked")
# 终态：不回 prepared，墓碑不释放。
TERMINAL_STATES = ("sent", "unknown", "revoked")

# 推送摘要字段簇（JEE-1956 整理项）：prepare 固定、send 二次复验逐项比对的基线字段。
SUMMARY_FIELDS = (
    "reportVersion",
    "publishable",
    "coverHash",
    "themeId",
    "accountRef",
    "accountVersion",
)


def os_fd_read(fd: int) -> bytes:
    import os

    with os.fdopen(fd, "rb") as f:
        return f.read()


def read_intents(run_id: str) -> list[dict[str, Any]]:
    """只读读取 intent 注册表；不存在返回空。损坏返回 None 语义（调用方决定）。"""
    paths.validate_run_id(run_id)
    try:
        fd, _st = paths.open_run_file_rel(run_id, _INTENTS_REL)
    except FacadeError:
        return []
    raw = os_fd_read(fd)
    return _parse_intents(raw)


def _parse_intents(raw: bytes) -> list[dict[str, Any]]:
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise state_corrupted("推送记录损坏")
    if not isinstance(data, dict):
        raise state_corrupted("推送记录损坏")
    items = data.get("intents", [])
    # 九轮-5：结构错误 fail-closed——intents 非 list、条目非 dict、缺 intentId 一律 STATE_CORRUPTED。
    if not isinstance(items, list):
        raise state_corrupted("推送记录结构损坏")
    for item in items:
        if not isinstance(item, dict):
            raise state_corrupted("推送记录结构损坏")
        if not isinstance(item.get("intentId"), str) or not item.get("intentId"):
            raise state_corrupted("推送记录结构损坏")
    return items


def _write_intents(run_id: str, intents: list[dict[str, Any]]) -> None:
    """写回 intent 注册表（调用方须已持 run 锁），schemaVersion=2 原子替换。"""
    storage.write_run_file_rel(
        run_id,
        _INTENTS_REL,
        json.dumps({"schemaVersion": SCHEMA_VERSION, "intents": intents}, ensure_ascii=False, indent=2).encode("utf-8"),
    )


def get_intent(run_id: str, intent_id: str) -> dict[str, Any] | None:
    for item in read_intents(run_id):
        if isinstance(item, dict) and item.get("intentId") == intent_id:
            return item
    return None


def record_intent(
    run_id: str,
    *,
    intent_id: str,
    workflow_id: str,
    generation: int,
    article_version: str | None,
    expires_at: str,
    report_version: str | None = None,
    publishable: bool | None = None,
    cover_hash: str = "",
    theme_id: str = "",
    account_ref: str = "",
    account_version: str | None = None,
    cover_asset_id: str = "",
) -> dict[str, Any]:
    """在 run 锁内登记一个新 intent（op_draft_prepare 调用），state=prepared。

    九轮-9 / JEE-1956 缺口 1：摘要字段（reportVersion/publishable/coverHash/themeId/
    accountRef/accountVersion）一并写入并作为发送二次复验的固定基线；coverAssetId
    用于发送时回读封面当前哈希比对。
    """
    paths.validate_run_id(run_id)
    intents = read_intents(run_id)
    intents = [i for i in intents if isinstance(i, dict)]
    if any(i.get("intentId") == intent_id for i in intents):
        raise forbidden("intentId 已存在")
    entry = {
        "intentId": intent_id,
        "runId": run_id,
        "workflowId": workflow_id,
        "generation": generation,
        "articleVersion": article_version,
        "expiresAt": expires_at,
        "state": "prepared",
        "operationId": None,
        "claimNonce": None,
        "claimedAt": None,
        "dispatchStartedAt": None,
        "result": None,
        "issue": None,
        "reportVersion": report_version,
        "publishable": publishable,
        "coverHash": cover_hash,
        "themeId": theme_id,
        "accountRef": account_ref,
        "accountVersion": account_version,
        "coverAssetId": cover_asset_id,
    }
    intents.append(entry)
    _write_intents(run_id, intents)
    return entry


def transition_intent(
    run_id: str,
    intent_id: str,
    *,
    expected_state: str | None,
    new_state: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """CAS 状态转移（调用方须已持 run 锁）。expected_state=None 表示不校验当前状态。

    返回新 entry；不匹配抛 FacadeError（VERSION_CONFLICT / INVALID_TRANSITION）。
    """
    from .errors import invalid_transition, version_conflict

    intents = read_intents(run_id)
    intents = [i for i in intents if isinstance(i, dict)]
    for idx, item in enumerate(intents):
        if item.get("intentId") != intent_id:
            continue
        cur = item.get("state", "prepared")
        if expected_state is not None and cur != expected_state:
            if cur in TERMINAL_STATES:
                raise invalid_transition(f"推送记录已是终态 {cur}")
            raise version_conflict("推送记录状态冲突，请刷新后重试")
        if cur in TERMINAL_STATES and new_state != cur:
            raise invalid_transition(f"终态 {cur} 不可转移为 {new_state}")
        item["state"] = new_state
        if extra:
            item.update(extra)
        intents[idx] = item
        _write_intents(run_id, intents)
        return item
    raise not_found("推送意图不存在")


def revoke_intents_except(
    run_id: str,
    *,
    keep_workflow_id: str,
    keep_generation: int,
) -> int:
    """撤销不属于 (workflowId, generation) 的未撤销 intent，返回撤销数。调用方须已持 run 锁。"""
    intents = read_intents(run_id)
    changed = 0
    for item in intents:
        if not isinstance(item, dict):
            continue
        if item.get("state") in TERMINAL_STATES:
            continue
        if item.get("workflowId") == keep_workflow_id and item.get("generation") == keep_generation:
            continue
        item["state"] = "revoked"
        item["issue"] = "已被接管撤销"
        changed += 1
    if changed:
        _write_intents(run_id, intents)
    return changed


def revoke_all_intents(run_id: str) -> int:
    """撤销全部未撤销 intent（cancel 路径），返回撤销数。调用方须已持 run 锁。"""
    intents = read_intents(run_id)
    changed = 0
    for item in intents:
        if isinstance(item, dict) and item.get("state") not in TERMINAL_STATES:
            item["state"] = "revoked"
            item["issue"] = "已被取消撤销"
            changed += 1
    if changed:
        _write_intents(run_id, intents)
    return changed


def revoked_intent_ids_except(run_id: str, *, keep_workflow_id: str, keep_generation: int) -> list[str]:
    """返回不属于 (workflowId, generation) 的未撤销 intentId 集合（只读，不写盘）。"""
    ids: list[str] = []
    for item in read_intents(run_id):
        if not isinstance(item, dict):
            continue
        if item.get("state") in TERMINAL_STATES:
            continue
        if item.get("workflowId") == keep_workflow_id and item.get("generation") == keep_generation:
            continue
        iid = item.get("intentId")
        if isinstance(iid, str) and iid:
            ids.append(iid)
    return ids


def all_intent_ids(run_id: str) -> list[str]:
    """返回全部 intentId（cancel 路径用于写进 binding 撤销集合）。只读。"""
    ids: list[str] = []
    for item in read_intents(run_id):
        if isinstance(item, dict):
            iid = item.get("intentId")
            if isinstance(iid, str) and iid:
                ids.append(iid)
    return ids


def active_intent_for(run_id: str, intent_id: str, *, workflow_id: str, generation: int) -> dict[str, Any] | None:
    """复验（唯一权威）：在 run 锁内同时读 binding + intents。

    - intentId 命中 binding.revokedIntentIds → 判死。
    - workflowId/generation 与 binding 不一致 → 判死。
    - state 为终态/revoked → 判死。
    调用方须已持 run 锁。
    """
    from . import binding

    current_binding = binding.read_binding(run_id)
    if isinstance(current_binding, dict):
        revoked = current_binding.get("revokedIntentIds")
        if isinstance(revoked, list) and intent_id in revoked:
            return None
        if current_binding.get("workflowId") != workflow_id or current_binding.get("generation") != generation:
            return None

    item = get_intent(run_id, intent_id)
    if not isinstance(item, dict):
        return None
    if item.get("state") in TERMINAL_STATES:
        return None
    return item
