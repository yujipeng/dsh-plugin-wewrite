"""业务 service：把 operation 分发到只读投影/写面/复用边界，返回业务联合 {ok,data|error}。

读写面副作用清单严格区分（设计 §6）：
- 读面（diagnose/snapshot/run.detail/artifact.get/asset.get/draft.status）绝不写盘。
- 写面（run.create/run.step/run.finish/run.fork/artifact.write/article.save/review.build 落盘/
  preview.render 落盘/asset.put/draft.prepare/draft.send）副作用如实。
"""

from __future__ import annotations

import json
import os
import uuid
from typing import Any

from . import assets, binding, contract, counters, draft_dispatch, hotspots as hotspots_mod, intents, learn, paths, projection, publish, review as review_mod, rewrite, stats, storage, style
from .errors import (
    FacadeError,
    business_failure,
    business_success,
    forbidden,
    invalid_input,
    not_found,
)

# 子进程自身环境设置固定 WEWRITE_HOME，不改 host 全局环境（由 runner/__main__ 设置）。
# 这里读取已设置好的 home。


def _diagnostics_for_uninitialized() -> list[dict[str, Any]]:
    return [{"code": "HOME_UNINITIALIZED", "message": "数据根未初始化，需先创建 run", "blocking": True}]


def _home_ready() -> bool:
    """数据根 + runs 目录 + 锁设施是否就绪（只读探测，不创建）。"""
    root = paths.home()
    if not os.path.isdir(root):
        return False
    if not os.path.isdir(os.path.join(root, "runs")):
        return False
    return True


def op_diagnose(payload: dict) -> dict[str, Any]:
    """诊断：依赖/数据根/主题/计数器，只读不写。"""
    from wewrite import __version__ as wewrite_version
    diags: list[dict[str, Any]] = []
    if not _home_ready():
        diags.append({"code": "HOME_UNINITIALIZED", "message": "数据根未初始化，需先创建 run", "blocking": False})
    themes = projection.list_theme_ids()
    if not themes:
        diags.append({"code": "THEME_MISSING", "message": "未找到可用主题", "blocking": False})
    counters_snapshot = counters.get_counters().snapshot()
    return business_success({
        "facadeVersion": "0.1.0",
        "wewriteVersion": wewrite_version,
        "protocol": contract.PROTOCOL,
        "homeInitialized": _home_ready(),
        "themes": themes,
        "diagnostics": diags,
        "counters": counters_snapshot,
    })


def op_snapshot(payload: dict) -> dict[str, Any]:
    """snapshot：只读投影全部 run 摘要 + 主题 + capabilities + diagnostics。"""
    return business_success(_snapshot_data())


def op_run_list(payload: dict) -> dict[str, Any]:
    """run/list：只读分页（简化：全量返回，nextCursor=None，符合首版范围）。"""
    snap = _snapshot_data()
    items = snap["runs"]
    limit = payload.get("limit")
    if limit is not None:
        items = items[:limit]
    return business_success({"items": items, "nextCursor": None})


def _snapshot_data() -> dict[str, Any]:
    diagnostics: list[dict[str, Any]] = []
    if not _home_ready():
        diagnostics.append({"code": "HOME_UNINITIALIZED", "message": "数据根未初始化，需先创建 run", "blocking": True})
        return {
            "runs": [],
            "nextCursor": None,
            "themes": projection.list_theme_ids(),
            "capabilities": {
                "read": False,
                "write": False,
                "agent": False,
                "skills": False,
                "preview": False,
                "push": False,
            },
            "diagnostics": diagnostics,
            "settings": {
                "visualMode": "none",
                "pushEnabled": False,
                "publishTransport": "fake",
                "credentialConfigured": False,
            },
        }

    runs = []
    for run_id in paths.list_run_ids():
        try:
            storage.check_no_pending_transaction(run_id)
            state, diags = projection.read_state(run_id)
        except FacadeError:
            continue
        runs.append(projection.run_summary(state, run_id))
        for d in diags:
            diagnostics.append({"code": d, "message": "状态不可读" if d == "STATE_CORRUPTED" else "状态为旧格式或需检查", "blocking": d == "STATE_CORRUPTED"})

    runs.sort(key=lambda r: r.get("updated", ""), reverse=True)

    return {
        "runs": runs,
        "nextCursor": None,
        "themes": projection.list_theme_ids(),
        "capabilities": {
            "read": True,
            "write": True,
            "agent": False,
            "skills": False,
            "preview": True,
            "push": False,
        },
        "diagnostics": diagnostics,
        "settings": {
            "visualMode": "none",
            "pushEnabled": False,
            "publishTransport": "fake",
            "credentialConfigured": False,
        },
    }


def op_hotspots(payload: dict) -> dict[str, Any]:
    return business_success(hotspots_mod.fetch_hotspots(payload["limit"]))


def op_run_create(payload: dict) -> dict[str, Any]:
    result = storage.create_run(
        topic=payload["topic"],
        mode=payload["mode"],
        visual_mode=payload["visualMode"],
        theme_id=payload["themeId"],
        request_id=payload["requestId"],
    )
    return business_success(result)


def op_run_detail(payload: dict) -> dict[str, Any]:
    run_id = payload["runId"]
    storage.check_no_pending_transaction(run_id)
    state, diags = projection.read_state(run_id)
    if "STATE_CORRUPTED" in diags:
        return business_success({
            "run": projection.run_summary(state, run_id),
            "binding": None,
            "steps": [],
            "artifacts": [],
            "gate": None,
            "lastError": {"code": "STATE_CORRUPTED", "message": "状态不可读，禁止编辑", "blocking": True},
            "push": None,
        })
    artifacts = projection.list_artifacts(state, run_id)
    gate = projection.build_gate(state, run_id)
    return business_success({
        "run": projection.run_summary(state, run_id),
        "binding": None,
        "steps": _steps_from_state(state),
        "artifacts": artifacts,
        "gate": gate,
        "lastError": None,
        "push": None,
    })


def _steps_from_state(state: dict) -> list[dict[str, Any]]:
    steps_state = state.get("steps") or {}
    out = []
    for step in contract.STEPS:
        s = steps_state.get(step) if isinstance(steps_state, dict) else None
        status = s.get("status") if isinstance(s, dict) else None
        error = s.get("error") if isinstance(s, dict) else None
        out.append({
            "step": step,
            "status": status if status in contract.STEP_STATUSES else "pending",
            "error": error if isinstance(error, str) else None,
        })
    return out


def op_run_step(payload: dict) -> dict[str, Any]:
    run_id = payload["runId"]
    with storage.run_lock(run_id, create=False):
        storage.ensure_writable_state(run_id)
        if payload.get("context"):
            binding.require_active_binding(run_id, payload["context"]["workflowId"],
                                           payload["context"]["generation"])
        _mark_step(run_id, payload["step"], payload["status"], payload.get("error"))
    return business_success({
        "runId": run_id,
        "step": payload["step"],
        "status": payload["status"],
    })


def _mark_step(run_id: str, step: str, status: str, error: str | None) -> None:
    """复用上游 mark_step（写面，显式 run_id）。"""
    from wewrite.runs import mark_step as _upstream
    try:
        _upstream(step, status, run_id=run_id, error=error)
    except ValueError as exc:
        raise invalid_input(str(exc)) from exc


def op_run_finish(payload: dict) -> dict[str, Any]:
    run_id = payload["runId"]
    with storage.run_lock(run_id, create=False):
        storage.ensure_writable_state(run_id)
        if payload.get("context"):
            binding.require_active_binding(run_id, payload["context"]["workflowId"],
                                           payload["context"]["generation"])

        # §9 定稿：封存必须在锁内先过同一报告门禁（不依赖上游「有 draft 才核 editorial」）。
        gate = projection.build_gate({}, run_id)
        if not isinstance(gate, dict) or gate.get("publishable") is not True:
            from .errors import FacadeError
            raise FacadeError("REVIEW_REQUIRED", "需要先完成审稿且通过", retryable=False)

        from wewrite.runs import finish_run as _upstream
        try:
            state = _upstream(run_id=run_id)
        except ValueError as exc:
            raise invalid_input(str(exc)) from exc
        status = state.get("status")
    return business_success({
        "runId": run_id,
        "pythonStatus": status,
    })


def op_run_fork(payload: dict) -> dict[str, Any]:
    result = storage.fork_article(payload["runId"], payload["expectedVersion"])
    return business_success(result)


def op_artifact_get(payload: dict) -> dict[str, Any]:
    run_id = payload["runId"]
    kind = payload["kind"]
    storage.check_no_pending_transaction(run_id)
    state, diags = projection.read_state(run_id)
    if "STATE_CORRUPTED" in diags:
        raise FacadeError("STATE_CORRUPTED", "状态不可读，无法读取产物", run_id=run_id)
    content, version = projection.read_artifact_content(run_id, kind)
    editable = projection.run_summary(state, run_id)["editable"]
    if kind == "review_report":
        # §9：审稿报告只读，不由通用写口或工作台编辑。
        editable = False
    return business_success({
        "runId": run_id,
        "kind": kind,
        "content": content,
        "version": version,
        "editable": editable,
    })


# 通用写口白名单（§9 方案 A）：review_report 只读，不由 artifact.write 生成。
_ARTIFACT_WRITE_WHITELIST = frozenset({
    "brief", "claims", "draft", "article", "sources",
    "illustrated_article", "image_prompts", "images_manifest",
})

# 改写审批摘要的 kind：影响门禁的内容写入需取 send 门闩（门闩 → run 锁）。
# review_report 已从通用写口移除（写入被白名单拒绝），不再列入本集合。
_SEND_LATCHED_KINDS = frozenset({"article", "draft", "brief", "claims", "sources"})


def _reject_review_report_write() -> FacadeError:
    from .errors import forbidden
    return forbidden("审稿报告仅能由审稿流程生成，请调用 wewrite_review")


def op_artifact_write(payload: dict) -> dict[str, Any]:
    run_id = payload["runId"]
    kind = payload["kind"]
    content = payload["content"]
    expected = payload["expectedVersion"]

    # §9：写口白名单在写入、版本读取、创建发送门闩之前检查；review_report 一律拒绝。
    _ensure_writable_kind(kind)

    # 影响门禁的 kind 取 send 门闩（门闩 → run 锁）；其余 kind 只取 run 短锁。
    if kind in _SEND_LATCHED_KINDS:
        with storage.send_lock(run_id, create=True):
            with storage.run_lock(run_id, create=False):
                return _artifact_write_locked(payload)
    with storage.run_lock(run_id, create=False):
        return _artifact_write_locked(payload)


def _ensure_writable_kind(kind: str) -> None:
    """写口白名单（§9 方案 A）：review_report 及其它未知 kind 一律 FORBIDDEN。"""
    if kind not in _ARTIFACT_WRITE_WHITELIST:
        if kind == "review_report":
            raise _reject_review_report_write()
        from .errors import invalid_input
        raise invalid_input("该 kind 不可写入")


def _artifact_write_locked(payload: dict) -> dict[str, Any]:
    run_id = payload["runId"]
    kind = payload["kind"]
    content = payload["content"]
    expected = payload["expectedVersion"]

    # §9：内部调用入口再次检查同一白名单，防止未来绕过。
    _ensure_writable_kind(kind)

    storage.ensure_writable_state(run_id)
    if payload.get("context"):
        binding.require_active_binding(run_id, payload["context"]["workflowId"],
                                       payload["context"]["generation"])
    current = _current_version(run_id, kind)
    if current != expected:
        from .errors import version_conflict
        raise version_conflict("内容版本冲突，请刷新后重试")
    # JSON/YAML 产物必须解析为对应结构，不能任意 blob 写入（设计 §4）。
    _validate_structured_artifact(kind, content)

    # sources 写入：缺失 evidence.sha256 由服务端补齐；显式给出但不相等则拒绝。
    if kind == "sources":
        content = _fill_sources_evidence_hashes(content)

    storage.write_run_file(run_id, paths.KIND_FILES[kind], content.encode("utf-8"))
    new_version = storage.sha256_hex(content.encode("utf-8"))
    return business_success({
        "runId": run_id,
        "kind": kind,
        "version": new_version,
    })


def _fill_sources_evidence_hashes(content: str) -> str:
    """sources 写入时为缺失 evidence.sha256 计算并补齐；显式不相等则拒绝。

    这是产物内容规则（§10 第 2 步），不增加工具参数/返回字段。
    """
    import yaml
    try:
        data = yaml.safe_load(content)
    except yaml.YAMLError:
        raise invalid_input("产物必须是合法 YAML")
    if not isinstance(data, dict):
        raise invalid_input("产物必须是 YAML 对象")
    changed = False
    for item in data.get("sources") if isinstance(data.get("sources"), list) else []:
        if not isinstance(item, dict):
            continue
        evidence = item.get("evidence")
        if not isinstance(evidence, dict):
            continue
        text = evidence.get("text")
        if not isinstance(text, str):
            continue
        computed = storage.sha256_hex(text.encode("utf-8"))
        given = evidence.get("sha256")
        if given is None:
            evidence["sha256"] = computed
            changed = True
        elif given != computed:
            raise invalid_input("evidence.sha256 与 evidence.text 不一致")
    if changed:
        return yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
    return content


def _current_version(run_id: str, kind: str) -> str | None:
    try:
        fd, _st = paths.open_artifact(run_id, kind)
    except FacadeError as exc:
        if exc.code == "NOT_FOUND":
            return None
        raise
    return storage.sha256_hex(os.fdopen(fd, "rb").read())


def _validate_structured_artifact(kind: str, content: str) -> None:
    """JSON/YAML 产物写入前必须解析为对应结构（设计 §4），否则 INVALID_INPUT。"""
    import yaml

    if kind == "images_manifest":
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            raise invalid_input("产物必须是合法 JSON")
        if not isinstance(data, dict):
            raise invalid_input("产物必须是 JSON 对象")
    elif kind in ("brief", "claims", "sources"):
        try:
            data = yaml.safe_load(content)
        except yaml.YAMLError:
            raise invalid_input("产物必须是合法 YAML")
        if not isinstance(data, dict):
            raise invalid_input("产物必须是 YAML 对象")


def op_article_save(payload: dict) -> dict[str, Any]:
    # 裁定 A：正文保存参与 send 门闩（门闩 → run 锁），发送在途时 RUN_BUSY。
    with storage.send_lock(payload["runId"], create=True):
        result = storage.article_save(
            payload["runId"],
            payload["markdown"],
            payload["expectedVersion"],
            payload["metadata"],
        )
    return business_success(result)


def op_review_build(payload: dict) -> dict[str, Any]:
    # 裁定 A：报告替换参与 send 门闩（门闩 → run 锁），发送在途时 RUN_BUSY。
    with storage.send_lock(payload["runId"], create=True):
        result = review_mod.build_review(
            run_id=payload["runId"],
            article_version=payload["articleVersion"],
            draft_version=payload["draftVersion"],
            assessment=payload["assessment"],
            context=payload.get("context"),
        )
    return business_success(result)


def op_preview_render(payload: dict) -> dict[str, Any]:
    result = review_mod.render_preview(
        run_id=payload["runId"],
        kind=payload["kind"],
        expected_version=payload["expectedVersion"],
        theme_id=payload["themeId"],
    )
    return business_success(result)


def op_asset_put(payload: dict) -> dict[str, Any]:
    # 裁定 A：封面(role=cover)参与 send 门闩（coverHash 进审批摘要）；inline 只取 run 短锁。
    if payload["role"] == "cover":
        with storage.send_lock(payload["runId"], create=True):
            result = assets.put_asset(
                run_id=payload["runId"],
                role=payload["role"],
                media_type=payload["mediaType"],
                base64_text=payload["base64"],
                expected_article_version=payload["expectedArticleVersion"],
            )
    else:
        result = assets.put_asset(
            run_id=payload["runId"],
            role=payload["role"],
            media_type=payload["mediaType"],
            base64_text=payload["base64"],
            expected_article_version=payload["expectedArticleVersion"],
        )
    return business_success(result)


def op_asset_get(payload: dict) -> dict[str, Any]:
    result = assets.get_asset(payload["runId"], payload["assetId"])
    return business_success(result)


def op_binding_publish(payload: dict) -> dict[str, Any]:
    result = binding.publish_binding(
        payload["runId"],
        workflow_id=payload["workflowId"],
        generation=payload["generation"],
        agent_id=payload["agentId"],
        session_id=payload["sessionId"],
        workspace_id=payload["workspaceId"],
        phase=payload["phase"],
        expected_generation=payload.get("expectedGeneration"),
    )
    return business_success(result)


def op_binding_get(payload: dict) -> dict[str, Any]:
    data = binding.read_binding(payload["runId"])
    if data is None:
        return business_success({"runId": payload["runId"], "binding": None})
    # 只返回契约 Binding 字段，不含内部 updated 等。
    return business_success({
        "runId": payload["runId"],
        "binding": {
            "runId": data.get("runId"),
            "agentId": data.get("agentId"),
            "sessionId": data.get("sessionId"),
            "workspaceId": data.get("workspaceId"),
            "workflowId": data.get("workflowId"),
            "generation": data.get("generation"),
            "phase": data.get("phase"),
        },
    })


def op_binding_revoke_intents(payload: dict) -> dict[str, Any]:
    """cancel 路径：先门闩→run 锁 CAS 撤销 + phase=cancelled（七轮-1）。"""
    revoked = binding.revoke_intents_for_cancel(payload["runId"], expected_generation=payload["expectedGeneration"])
    return business_success({"runId": payload["runId"], "revoked": revoked})


def op_draft_prepare(payload: dict) -> dict[str, Any]:
    """薄 Adapter：调用 draft_dispatch.prepare。"""
    return draft_dispatch.prepare(payload)


def op_draft_send(payload: dict) -> dict[str, Any]:
    """薄 Adapter：调用 draft_dispatch.send（context 由 host 可信绑定注入）。"""
    return draft_dispatch.send(payload, payload["context"])


def op_draft_status(payload: dict) -> dict[str, Any]:
    """薄 Adapter：调用 draft_dispatch.status（真实只读投影，不固定返回成功）。"""
    return draft_dispatch.status(payload["runId"], payload["operationId"])


def op_draft_recover(payload: dict) -> dict[str, Any]:
    """私有恢复：调用 draft_dispatch.recover。"""
    return draft_dispatch.recover(payload)


# ── 方案 B 批一：style / learn / exemplar 账号级能力 ────────────────────────

def op_style_get(payload: dict) -> dict[str, Any]:
    return business_success(style.style_get())


def op_style_put(payload: dict) -> dict[str, Any]:
    result = style.style_put(payload)
    return business_success(result)


def op_style_validate(payload: dict) -> dict[str, Any]:
    return business_success(style.style_validate(payload))


def op_learn_record(payload: dict) -> dict[str, Any]:
    return business_success(learn.learn_record(payload))


def op_learn_list(payload: dict) -> dict[str, Any]:
    return business_success(learn.learn_list())


def op_exemplar_put(payload: dict) -> dict[str, Any]:
    return business_success(learn.exemplar_put(payload))


def op_exemplar_list(payload: dict) -> dict[str, Any]:
    return business_success(learn.exemplar_list())


def op_exemplar_get(payload: dict) -> dict[str, Any]:
    return business_success(learn.exemplar_get(payload))


# ── 方案 B 批二：stats / rewrite 账号级能力 ────────────────────────────────

def op_stats_fetch(payload: dict) -> dict[str, Any]:
    return business_success(stats.stats_fetch(payload))


def op_stats_backfill(payload: dict) -> dict[str, Any]:
    return business_success(stats.stats_backfill(payload))


def op_rewrite_score(payload: dict) -> dict[str, Any]:
    return business_success(rewrite.rewrite_score(payload))


def op_rewrite_similarity(payload: dict) -> dict[str, Any]:
    return business_success(rewrite.rewrite_similarity(payload))


_DISPATCH = {
    "diagnose": op_diagnose,
    "snapshot": op_snapshot,
    "run.list": op_run_list,
    "hotspots.fetch": op_hotspots,
    "run.create": op_run_create,
    "run.detail": op_run_detail,
    "run.step": op_run_step,
    "run.finish": op_run_finish,
    "run.fork": op_run_fork,
    "artifact.get": op_artifact_get,
    "artifact.write": op_artifact_write,
    "article.save": op_article_save,
    "review.build": op_review_build,
    "preview.render": op_preview_render,
    "asset.put": op_asset_put,
    "asset.get": op_asset_get,
    "binding.publish": op_binding_publish,
    "binding.get": op_binding_get,
    "binding.revoke-intents": op_binding_revoke_intents,
    "draft.prepare": op_draft_prepare,
    "draft.send": op_draft_send,
    "draft.status": op_draft_status,
    "draft.recover": op_draft_recover,
    "style.get": op_style_get,
    "style.put": op_style_put,
    "style.validate": op_style_validate,
    "learn.record": op_learn_record,
    "learn.list": op_learn_list,
    "exemplar.put": op_exemplar_put,
    "exemplar.list": op_exemplar_list,
    "exemplar.get": op_exemplar_get,
    "stats.fetch": op_stats_fetch,
    "stats.backfill": op_stats_backfill,
    "rewrite.score": op_rewrite_score,
    "rewrite.similarity": op_rewrite_similarity,
}


def dispatch(operation: str, payload: dict) -> dict[str, Any]:
    """分发 operation，返回业务联合。异常统一转业务失败（中文、无栈）。"""
    try:
        contract.validate_operation(operation)
        validated = contract.validate_payload(operation, payload)
        fn = _DISPATCH[operation]
        return fn(validated)
    except FacadeError as exc:
        return business_failure(exc.code, str(exc), retryable=exc.retryable,
                                **(dict(run_id=exc.run_id) if exc.run_id else {}))
    except Exception:
        # 不泄露异常原文/栈；诊断留系统日志（stderr）。
        return business_failure("INTERNAL", "内部错误", retryable=False)
