"""intent 注册表（设计 §5 接管栅栏 + §6 存储布局 + 返修 6）。

推送 intent 持久化在 ``runs/<runId>/.dsh-workbench/intents.json``，每个 intent 绑定发起时的
``workflowId``/``generation``/``runId``/``articleVersion``。接管/取消时在 run 锁内把旧 generation
的全部未消费 intent 标记 ``revoked``，与 binding 发布写进同一可恢复事务；撤销后旧 intent 的
外部调用计数为零（draft.send 复验时拒绝 revoked intent）。

注意：S3 无真实推送，intent 只用于预检；但撤销栅栏必须完整落地，S4 真实推送直接复用。
"""

from __future__ import annotations

import json
from typing import Any

from . import paths, storage
from .errors import FacadeError, forbidden

_INTENTS_REL = ".dsh-workbench/intents.json"


def read_intents(run_id: str) -> list[dict[str, Any]]:
    """只读读取 intent 注册表；不存在返回空。绝不创建目录/文件。"""
    paths.validate_run_id(run_id)
    try:
        fd, _st = paths.open_run_file_rel(run_id, _INTENTS_REL)
    except FacadeError:
        return []
    raw = os_fd_read(fd)
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return []
    if not isinstance(data, dict):
        return []
    items = data.get("intents", [])
    return items if isinstance(items, list) else []


def os_fd_read(fd: int) -> bytes:
    import os

    with os.fdopen(fd, "rb") as f:
        return f.read()


def _write_intents(run_id: str, intents: list[dict[str, Any]]) -> None:
    """写回 intent 注册表（调用方须已持 run 锁）。"""
    storage.write_run_file_rel(
        run_id,
        _INTENTS_REL,
        json.dumps({"intents": intents}, ensure_ascii=False, indent=2).encode("utf-8"),
    )


def record_intent(
    run_id: str,
    *,
    intent_id: str,
    workflow_id: str,
    generation: int,
    article_version: str | None,
    expires_at: str,
) -> dict[str, Any]:
    """在 run 锁内登记一个新 intent（op_draft_prepare 调用）。"""
    paths.validate_run_id(run_id)
    intents = read_intents(run_id)
    intents = [i for i in intents if isinstance(i, dict)]
    entry = {
        "intentId": intent_id,
        "runId": run_id,
        "workflowId": workflow_id,
        "generation": generation,
        "articleVersion": article_version,
        "expiresAt": expires_at,
        "revoked": False,
    }
    intents.append(entry)
    _write_intents(run_id, intents)
    return entry


def revoke_intents_except(
    run_id: str,
    *,
    keep_workflow_id: str,
    keep_generation: int,
) -> int:
    """撤销不属于指定 (workflowId, generation) 的全部未撤销 intent，返回撤销数。

    调用方须已持 run 锁（与 binding 发布同一事务）。
    """
    intents = read_intents(run_id)
    changed = 0
    for item in intents:
        if not isinstance(item, dict):
            continue
        if item.get("revoked") is True:
            continue
        if item.get("workflowId") == keep_workflow_id and item.get("generation") == keep_generation:
            continue
        item["revoked"] = True
        changed += 1
    if changed:
        _write_intents(run_id, intents)
    return changed


def revoke_all_intents(run_id: str) -> int:
    """撤销全部未撤销 intent（cancel 路径），返回撤销数。调用方须已持 run 锁。"""
    intents = read_intents(run_id)
    changed = 0
    for item in intents:
        if isinstance(item, dict) and item.get("revoked") is not True:
            item["revoked"] = True
            changed += 1
    if changed:
        _write_intents(run_id, intents)
    return changed


def revoked_intent_ids_except(run_id: str, *, keep_workflow_id: str, keep_generation: int) -> list[str]:
    """返回不属于 (workflowId, generation) 的未撤销 intentId 集合（只读，不写盘）。

    供 binding.publish 把它与新绑定合成**单一提交记录**（返修二轮-3：跨文件原子性）。
    """
    ids: list[str] = []
    for item in read_intents(run_id):
        if not isinstance(item, dict):
            continue
        if item.get("revoked") is True:
            continue
        if item.get("workflowId") == keep_workflow_id and item.get("generation") == keep_generation:
            continue
        iid = item.get("intentId")
        if isinstance(iid, str) and iid:
            ids.append(iid)
    return ids


def apply_revocations(run_id: str, revoked_ids: list[str]) -> int:
    """幂等重放撤销：把指定 intentId 集合标记 revoked（commit 记录重放用）。调用方须已持 run 锁。"""
    if not revoked_ids:
        return 0
    ids = set(revoked_ids)
    intents = read_intents(run_id)
    changed = 0
    for item in intents:
        if isinstance(item, dict) and item.get("revoked") is not True and item.get("intentId") in ids:
            item["revoked"] = True
            changed += 1
    if changed:
        _write_intents(run_id, intents)
    return changed


def active_intent_for(run_id: str, intent_id: str, *, workflow_id: str, generation: int) -> dict[str, Any] | None:
    """复验：仅返回未撤销且绑定一致的 intent；否则 None。draft.send 占用前必须调用。"""
    for item in read_intents(run_id):
        if not isinstance(item, dict):
            continue
        if item.get("intentId") != intent_id:
            continue
        if item.get("revoked") is True:
            return None
        if item.get("workflowId") != workflow_id or item.get("generation") != generation:
            return None
        return item
    return None
