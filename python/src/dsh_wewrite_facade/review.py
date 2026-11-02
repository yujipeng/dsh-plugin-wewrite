"""审稿与预览（复用上游 build_report / WeChatConverter / preview_html，无网络上传）。

- review.build：锁定快照读 article/draft/brief/claims/sources 五份产物 → 清洗模型自由文本 →
  复用上游 build_report → editorial.compute_verdict 做确定性结构校验 → 落盘
  review-report.json（含 editorial_verdict / input_hashes / model_assessment 内部字段）→
  返回严格 ReportSchema 投影（对外无内部字段）。
- preview.render：复用 WeChatConverter.convert()/preview_html() 排版，不做任何网络上传。
"""

from __future__ import annotations

import json
from typing import Any

from .errors import FacadeError, invalid_input, not_found
from . import binding, editorial, projection, sanitize, storage


def _read_artifact_or_none(run_id: str, kind: str) -> tuple[str | None, str | None]:
    try:
        content, version = projection.read_artifact_content(run_id, kind)
        return content, version
    except FacadeError:
        return None, None


def _parse_yaml(content: str | None) -> dict:
    if content is None:
        return {}
    import yaml
    try:
        data = yaml.safe_load(content)
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def _parse_yaml_reject_dup(content: str | None) -> dict:
    """解析 YAML 且拒绝重复键（返回 {} 表示缺失/损坏/重复键）。"""
    if content is None:
        return {}
    import yaml
    try:
        # yaml.safe_load 默认覆盖重复键；这里用 SafeLoader 显式拒绝重复键。
        class _NoDupLoader(yaml.SafeLoader):
            pass

        def _no_dup(loader, node, deep=False):
            mapping = {}
            for key_node, value_node in node.value:
                key = loader.construct_object(key_node, deep=deep)
                if key in mapping:
                    raise yaml.YAMLError(f"重复键: {key}")
                mapping[key] = loader.construct_object(value_node, deep=deep)
            return mapping

        _NoDupLoader.add_constructor(
            yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _no_dup
        )
        data = yaml.load(content, Loader=_NoDupLoader)
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def _snapshot_hashes(run_id: str) -> tuple[dict[str, str | None], dict[str, str | None]]:
    """读五份产物，返回 (contents, hashes)。缺失为 None。"""
    kinds = ("article", "draft", "brief", "claims", "sources")
    contents: dict[str, str | None] = {}
    hashes: dict[str, str | None] = {}
    for k in kinds:
        content, version = _read_artifact_or_none(run_id, k)
        contents[k] = content
        hashes[k] = version
    return contents, hashes


def _public_notes(items: list[dict[str, Any]]) -> str:
    payload = {
        "version": 1,
        "scope": "引用链与声明结构校验，未验证语义与来源真实性",
        "items": [
            {
                "rule": it.get("rule"),
                "id": it.get("id"),
                "state": it.get("state"),
                "code": it.get("code"),
                "reason": it.get("reason"),
            }
            for it in items
        ],
    }
    return "守卫诊断：" + json.dumps(payload, ensure_ascii=False)


def _report_public_fields(report: dict[str, Any]) -> dict[str, Any]:
    """严格 ReportSchema 投影字段（与 src/shared/contract.ts ReportSchema 一致）。"""
    return {
        "version": report.get("version"),
        "decision": report.get("decision"),
        "pass_number": report.get("pass_number"),
        "publishable": report.get("publishable"),
        "dimensions": report.get("dimensions"),
        "dimension_average": report.get("dimension_average"),
        "blockers": report.get("blockers"),
        "major_issues": report.get("major_issues"),
        "notes": report.get("notes"),
        "draft_chars": report.get("draft_chars"),
        "final_chars": report.get("final_chars"),
        "edit_ratio": report.get("edit_ratio"),
        "structure_changed": report.get("structure_changed"),
    }


def build_review(*, run_id: str, article_version: str | None, draft_version: str | None,
                 assessment: dict[str, Any], context: dict[str, Any] | None = None) -> dict[str, Any]:
    """复用上游 build_report + editorial 结构校验。返回 public report + gate。"""
    contents, hashes = _snapshot_hashes(run_id)

    article = contents.get("article")
    if article is None:
        raise not_found("正文缺失")
    article_actual = hashes["article"]
    draft = contents.get("draft") or ""
    draft_actual = hashes["draft"]

    if article_version is not None and article_version != article_actual:
        from .errors import version_conflict
        raise version_conflict("正文版本已变化，请刷新后重试")
    if draft_version is not None and draft_version != draft_actual:
        from .errors import version_conflict
        raise version_conflict("初稿版本已变化，请刷新后重试")

    # 1) 清洗模型自由文本（notes / blockers / major_issues），保留 blocker 条目。
    scrubbed_assessment = editorial.scrub_assessment(assessment)

    # 2) 复用上游 build_report（用清洗后的 assessment）。
    try:
        from wewrite.commands.content_eval import build_report as _upstream
        report = _upstream(draft or article, article, scrubbed_assessment)
    except ValueError as exc:
        raise invalid_input(str(exc)) from exc

    # 3) 解析产物（拒绝 YAML 重复键）。
    brief_data = _parse_yaml_reject_dup(contents.get("brief"))
    claims_data = _parse_yaml_reject_dup(contents.get("claims"))
    sources_data = _parse_yaml_reject_dup(contents.get("sources"))

    personal_available = None
    brief_pm = brief_data.get("personal_materials") if isinstance(brief_data, dict) else None
    if isinstance(brief_pm, dict) and isinstance(brief_pm.get("available"), bool):
        personal_available = brief_pm.get("available")

    # 4) editorial 结构校验。
    verdict = editorial.compute_verdict(
        article=article,
        brief=brief_data,
        claims=claims_data,
        sources=sources_data,
        personal_materials_available=personal_available,
    )
    items = verdict.get("items", [])
    blocking = editorial.has_blocking(verdict)

    # 5) 组装公开报告字段（严格 ReportSchema）。
    model_blockers = [f"模型意见：{b}" for b in report.get("blockers") or []]
    model_major_issues = [f"模型意见：{m}" for m in report.get("major_issues") or []]
    guard_blocker_texts = [f"[{it.get('code')}] {it.get('reason', '')}" for it in items
                           if it.get("state") in (editorial.UNKNOWN, editorial.UNSUPPORTED)]

    public_report = _report_public_fields(report)
    public_report["notes"] = _public_notes(items)
    public_report["blockers"] = model_blockers + guard_blocker_texts
    public_report["major_issues"] = model_major_issues

    if blocking:
        public_report["decision"] = "revise"
        public_report["publishable"] = False

    # 6) 落盘磁盘报告（含内部字段）。
    disk_report = dict(public_report)
    disk_report["article_hash"] = article_actual
    disk_report["draft_hash"] = draft_actual
    disk_report["editorial_schema"] = editorial.EDITORIAL_SCHEMA
    disk_report["input_hashes"] = {
        "article": hashes["article"],
        "draft": hashes["draft"],
        "brief": hashes["brief"],
        "claims": hashes["claims"],
        "sources": hashes["sources"],
    }
    disk_report["editorial_verdict"] = {
        "version": 1,
        "producer": "facade_editorial_guard",
        "input_hashes": disk_report["input_hashes"],
        "items": items,
    }
    disk_report["model_assessment"] = {
        "notes": scrubbed_assessment.get("notes", ""),
    }

    with storage.run_lock(run_id, create=False):
        storage.ensure_writable_state(run_id)
        if context:
            binding.require_active_binding(run_id, context["workflowId"], context["generation"])
        storage.write_run_file(run_id, "review-report.json",
                               json.dumps(disk_report, ensure_ascii=False, indent=2).encode("utf-8"))
        storage.patch_state_editorial_review(
            run_id,
            decision=public_report.get("decision"),
            publishable=bool(public_report.get("publishable")),
        )
    report_version = storage.sha256_hex(json.dumps(disk_report, ensure_ascii=False, indent=2).encode("utf-8"))

    gate = {
        "publishable": bool(public_report.get("publishable")) and not blocking,
        "issues": [] if (public_report.get("publishable") and not blocking) else [
            {"code": "REVIEW_REQUIRED", "message": "需要先完成审稿", "blocking": True}
        ],
        "articleVersion": article_actual,
        "reportVersion": report_version,
    }
    return {
        "runId": run_id,
        "report": public_report,
        "reportVersion": report_version,
        "gate": gate,
    }


def render_preview(*, run_id: str, kind: str, expected_version: str | None, theme_id: str) -> dict[str, Any]:
    """复用 WeChatConverter 排版生成 HTML 预览。不做任何网络上传。"""
    if kind not in ("article", "illustrated_article"):
        raise invalid_input("kind 不合法")
    artifact_kind = "article" if kind == "article" else "illustrated_article"
    try:
        content, version = projection.read_artifact_content(run_id, artifact_kind)
    except FacadeError as exc:
        if exc.code == "NOT_FOUND":
            raise not_found("正文缺失") from exc
        raise
    if expected_version is not None and expected_version != version:
        from .errors import version_conflict
        raise version_conflict("正文版本已变化，请刷新后重试")

    try:
        from wewrite.toolkit.converter import WeChatConverter
        converter = WeChatConverter(theme_name=theme_id)
        result = converter.convert(content)
        html = result.html
    except Exception as exc:
        raise FacadeError("INTERNAL", f"预览转换失败：{type(exc).__name__}") from exc

    html = sanitize.sanitize_html(html)

    return {
        "runId": run_id,
        "version": version,
        "html": html,
        "issues": [],
        "remoteImagesUploaded": False,
    }
