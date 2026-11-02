"""审稿与预览（复用上游 build_report / WeChatConverter / preview_html，无网络上传）。

- review.build：复用 wewrite.commands.content_eval.build_report，另保存 article/draft 哈希
  与 assessment 来源（本 stage 返回内存结果，落盘在 artifact.write 显式触发）。
- preview.render：复用 WeChatConverter.convert()/preview_html() 排版，不做任何网络上传。
"""

from __future__ import annotations

import json
from typing import Any

from .errors import FacadeError, invalid_input, not_found
from . import binding, editorial, projection, sanitize, storage


def build_review(*, run_id: str, article_version: str | None, draft_version: str | None,
                 assessment: dict[str, Any], context: dict[str, Any] | None = None) -> dict[str, Any]:
    """复用上游 build_report。返回 report + gate。不写盘（写盘由 artifact.write 显式触发）。"""
    # 读正文与初稿（只读，不写盘）。
    try:
        article, article_actual = projection.read_artifact_content(run_id, "article")
    except FacadeError as exc:
        if exc.code == "NOT_FOUND":
            raise not_found("正文缺失") from exc
        raise
    try:
        draft, draft_actual = projection.read_artifact_content(run_id, "draft")
    except FacadeError:
        draft, draft_actual = "", None

    if article_version is not None and article_version != article_actual:
        from .errors import version_conflict
        raise version_conflict("正文版本已变化，请刷新后重试")
    if draft_version is not None and draft_version != draft_actual:
        from .errors import version_conflict
        raise version_conflict("初稿版本已变化，请刷新后重试")

    try:
        from wewrite.commands.content_eval import build_report as _upstream
        report = _upstream(draft or article, article, assessment)
    except ValueError as exc:
        raise invalid_input(str(exc)) from exc

    # 方案 A：编辑红线守卫——审稿伪 pass 拦截（不依赖模型自报分数）。
    # 读 claims / sources / brief 的 personal_materials，判定四条「不可交付」红线；
    # 命中即降级为 revise，并把红线条目并入 blockers，使 build_report 的 publishable 归 false。
    claims_data = _read_yaml_artifact(run_id, "claims")
    sources_data = _read_yaml_artifact(run_id, "sources")
    brief_data = _read_yaml_artifact(run_id, "brief")
    personal_available = None
    brief_pm = brief_data.get("personal_materials") if isinstance(brief_data, dict) else None
    if isinstance(brief_pm, dict) and isinstance(brief_pm.get("available"), bool):
        personal_available = brief_pm.get("available")

    red_lines = editorial.editorial_blockers(
        article=article,
        claims=claims_data,
        sources=sources_data,
        personal_materials_available=personal_available,
    )
    if red_lines:
        report["decision"] = "revise"
        report["publishable"] = False
        report["blockers"] = list(dict.fromkeys([*(report.get("blockers") or []), *red_lines]))
        report["red_lines"] = red_lines

    report["article_hash"] = article_actual
    report["draft_hash"] = draft_actual
    report["assessment_source"] = "facade"

    # 落盘审稿报告（写面，显式写操作）：review-report.json 绑定 article_hash。
    # 同时把 decision/publishable 就地补丁进 state.editorial（finish_run 的封存校验同时读
    # editorial.decision/publishable 与 report.decision/publishable，两者都必须 pass/true）。
    with storage.run_lock(run_id, create=False):
        storage.ensure_writable_state(run_id)
        if context:
            binding.require_active_binding(run_id, context["workflowId"], context["generation"])
        report["article_hash"] = article_actual
        storage.write_run_file(run_id, "review-report.json",
                               json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8"))
        storage.patch_state_editorial_review(
            run_id,
            decision=report.get("decision"),
            publishable=bool(report.get("publishable")),
        )
    report_version = storage.sha256_hex(json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8"))

    gate = {
        "publishable": bool(report.get("publishable")),
        "issues": [] if report.get("publishable") else [
            {"code": "REVIEW_REQUIRED", "message": "需要先完成审稿", "blocking": True}
        ],
        "articleVersion": article_actual,
        "reportVersion": report_version,
    }
    return {
        "runId": run_id,
        "report": report,
        "reportVersion": report_version,
        "gate": gate,
    }


def _read_yaml_artifact(run_id: str, kind: str) -> dict | None:
    """只读解析 YAML 产物（claims/sources/brief）；缺失或损坏返回 None，不阻断审稿主链。"""
    try:
        content, _version = projection.read_artifact_content(run_id, kind)
    except FacadeError:
        return None
    import yaml

    try:
        data = yaml.safe_load(content)
    except yaml.YAMLError:
        return None
    return data if isinstance(data, dict) else None


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

    # 返修 5：返回前做 HTML 白名单清洗 + CSP（剔除 script/事件属性/iframe/远程 URL，
    # 远程图片改写为 data 占位，CSP 禁外连）。
    html = sanitize.sanitize_html(html)

    return {
        "runId": run_id,
        "version": version,
        "html": html,
        "issues": [],
        "remoteImagesUploaded": False,
    }
