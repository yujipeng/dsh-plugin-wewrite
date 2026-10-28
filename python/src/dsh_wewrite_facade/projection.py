"""只读投影（设计 §6）：yaml.safe_load + 显式 schema + 内存补默认，绝不写盘。

读面只调用本模块与 paths 的只读打开函数；禁止 load_run/resume_run/ensure_home/
_upgrade_state 等任何会写盘的读路径。旧格式缺字段内存补默认并返回诊断；
损坏状态显示不可读（STATE_CORRUPTED 诊断）而不是消失；snapshot/list 不触发迁移。
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

import yaml

from . import paths
from .errors import FacadeError, forbidden, internal, not_found

# 文本产物字节上限（与契约 TEXT_MAX_BYTES = 2 MiB）。
TEXT_MAX_BYTES = 2 * 1024 * 1024

PHASES = ("created", "starting", "writing", "reviewing", "ready", "failed", "cancelling", "cancelled", "interrupted")
STEPS = ("topic", "write", "review", "visual", "publish")
STEP_STATUSES = ("pending", "in_progress", "completed", "failed", "skipped")


def _now_rfc3339() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _sha256(data: bytes) -> str:
    import hashlib
    return hashlib.sha256(data).hexdigest()


def _as_str(value: Any, default: str = "") -> str:
    return value if isinstance(value, str) else default


def _as_bool(value: Any, default: bool = False) -> bool:
    return value if isinstance(value, bool) else default


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _normalize_phase(value: Any) -> str:
    if value == "active":
        return "writing"
    if value == "completed":
        return "ready"
    if value == "failed":
        return "failed"
    if value in PHASES:
        return value
    return "unknown"


def read_state(run_id: str) -> tuple[dict, list[str]]:
    """只读解析 state.yaml；返回 (state, diagnostics)。不升级、不写盘。

    - v4 最新格式：正常解析。
    - 旧格式缺字段：内存补默认，diagnostics 带 STATE_LEGACY。
    - 损坏（非 YAML / 非 dict）：抛 NOT_FOUND 之外的诊断语义——返回空投影 +
      STATE_CORRUPTED 诊断，不崩溃（读面遇到损坏显示不可读而非消失）。
    """
    try:
        fd, _st = paths.open_state(run_id)
    except FacadeError as exc:
        if exc.code == "NOT_FOUND":
            raise not_found("run 状态不存在") from exc
        raise
    with os.fdopen(fd, "r", encoding="utf-8") as f:
        raw = f.read()
    try:
        parsed = yaml.safe_load(raw)
    except yaml.YAMLError:
        return _empty_state(run_id), ["STATE_CORRUPTED"]
    if not isinstance(parsed, dict):
        return _empty_state(run_id), ["STATE_CORRUPTED"]

    diagnostics: list[str] = []
    if parsed.get("run_id") != run_id:
        # state.run_id 与路径不符：路径安全要求「先正则再校验 state.run_id」。
        raise forbidden("state 与 runId 不符")

    version = parsed.get("version")
    if not isinstance(version, int) or version < 4:
        diagnostics.append("STATE_LEGACY")

    # 内存补默认（不写盘），保持与上游 create_run 输出同构。
    topic = _as_dict(parsed.get("topic"))
    artifacts = _as_dict(parsed.get("artifacts"))
    state = {
        "version": version if isinstance(version, int) else 1,
        "run_id": run_id,
        "created": _as_str(parsed.get("created"), ""),
        "updated": _as_str(parsed.get("updated"), ""),
        "status": _as_str(parsed.get("status"), "active"),
        "mode": _as_str(parsed.get("mode"), "draft"),
        "permissions": _as_dict(parsed.get("permissions")),
        "visual": _as_dict(parsed.get("visual")),
        "topic": topic,
        "artifacts": artifacts,
        "steps": _as_dict(parsed.get("steps")),
        "last_error": parsed.get("last_error"),
    }
    # 旧格式补 artifact 相对路径（内存，不落盘）。
    for kind, fname in paths.KIND_FILES.items():
        if kind in ("preview",):
            continue
        if not artifacts.get(kind):
            artifacts[kind] = f"runs/{run_id}/{fname}"
    return state, diagnostics


def _empty_state(run_id: str) -> dict:
    return {
        "version": 1,
        "run_id": run_id,
        "created": "",
        "updated": "",
        "status": "active",
        "mode": "draft",
        "permissions": {},
        "visual": {},
        "topic": {},
        "artifacts": {},
        "steps": {},
        "last_error": None,
    }


def artifact_projection(state: dict, run_id: str, kind: str) -> dict[str, Any]:
    """读取单个产物，返回 Artifact 投影 {kind,exists,nonEmpty,version,bytes,mediaType}。"""
    if kind not in paths.KIND_FILES:
        raise not_found("产物类型不合法")
    try:
        fd, st = paths.open_artifact(run_id, kind)
    except FacadeError as exc:
        if exc.code == "NOT_FOUND":
            return {
                "kind": kind,
                "exists": False,
                "nonEmpty": False,
                "version": None,
                "bytes": 0,
                "mediaType": _media_type(kind),
            }
        raise
    with os.fdopen(fd, "rb") as f:
        data = f.read()
    return {
        "kind": kind,
        "exists": True,
        "nonEmpty": len(data) > 0,
        "version": _sha256(data),
        "bytes": len(data),
        "mediaType": _media_type(kind),
    }


def _media_type(kind: str) -> str:
    if kind in ("review_report", "images_manifest"):
        return "application/json"
    if kind in ("brief", "claims", "sources"):
        return "application/yaml"
    if kind == "preview":
        return "text/html"
    return "text/markdown"


def list_artifacts(state: dict, run_id: str) -> list[dict[str, Any]]:
    return [artifact_projection(state, run_id, k) for k in paths.KIND_FILES]


def read_artifact_content(run_id: str, kind: str) -> tuple[str, str | None]:
    """读取产物文本内容；返回 (content, version)。缺失抛 NOT_FOUND。"""
    if kind not in paths.KIND_FILES:
        raise not_found("产物类型不合法")
    fd, _st = paths.open_artifact(run_id, kind)
    with os.fdopen(fd, "r", encoding="utf-8") as f:
        content = f.read()
    if len(content.encode("utf-8")) > TEXT_MAX_BYTES:
        raise forbidden("内容超限")
    return content, _sha256(content.encode("utf-8"))


def run_summary(state: dict, run_id: str) -> dict[str, Any]:
    topic = _as_dict(state.get("topic"))
    title = _as_str(topic.get("title")) or _as_str(state.get("seo", {}).get("title") if isinstance(state.get("seo"), dict) else "")
    phase = _normalize_phase(state.get("status"))
    return {
        "runId": run_id,
        "title": title or "未命名文章",
        "mode": _as_str(state.get("mode"), "draft"),
        "pythonStatus": _as_str(state.get("status"), "active"),
        "phase": phase,
        "updated": _as_str(state.get("updated")) or _now_rfc3339(),
        "editable": _editable(state),
    }


def _editable(state: dict) -> bool:
    status = state.get("status")
    return status != "completed"


def build_gate(state: dict, run_id: str) -> dict[str, Any] | None:
    """从 state 与 review_report 投影 Gate；未审稿或审稿失效返回 None。

    除审稿文件的 publishable 外，还必须核对审稿绑定的正文哈希与当前正文哈希一致，
    不一致即不展示 pass（正文已更新、旧审稿未删、journal 未提交 → 不返回 pass）。
    """
    from . import storage
    storage.check_no_pending_transaction(run_id)

    report_version = None
    try:
        fd, _st = paths.open_artifact(run_id, "review_report")
    except FacadeError:
        return None
    with os.fdopen(fd, "rb") as f:
        raw = f.read()
    if not raw:
        return None
    report_version = _sha256(raw)
    try:
        report = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(report, dict):
        return None

    # 当前正文哈希。
    article_version = None
    try:
        fd2, _st2 = paths.open_artifact(run_id, "article")
    except FacadeError:
        pass
    else:
        with os.fdopen(fd2, "rb") as f2:
            article_version = _sha256(f2.read())

    # 审稿绑定的正文哈希（report.article_hash 或 report.articleVersion）。
    bound_hash = report.get("article_hash") or report.get("articleVersion")
    if bound_hash is not None and article_version is not None and bound_hash != article_version:
        # 正文已更新、旧审稿未删：审稿失效，不展示 pass。
        return {
            "publishable": False,
            "issues": [
                {"code": "REVIEW_REQUIRED", "message": "正文已更新，需要重新审稿", "blocking": True}
            ],
            "articleVersion": article_version,
            "reportVersion": report_version,
        }

    publishable = bool(report.get("publishable"))
    issues = [
        {"code": "REVIEW_REQUIRED", "message": "需要先完成审稿", "blocking": True}
        if not publishable
        else {"code": "REVIEW_PASSED", "message": "审稿通过", "blocking": False}
    ]
    return {
        "publishable": publishable,
        "issues": issues,
        "articleVersion": article_version or ("a" * 64),
        "reportVersion": report_version,
    }


def read_sources(run_id: str) -> dict[str, Any]:
    try:
        fd, _st = paths.open_artifact(run_id, "sources")
    except FacadeError as exc:
        if exc.code == "NOT_FOUND":
            return {"version": 1, "run_id": run_id, "sources": []}
        raise
    with os.fdopen(fd, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f.read()) or {}
    if not isinstance(data, dict):
        data = {}
    sources = data.get("sources", [])
    return {"version": 1, "run_id": run_id, "sources": sources if isinstance(sources, list) else []}


def list_theme_ids() -> list[dict[str, str]]:
    """列出可用主题（内置 + 用户 themes 目录）；读面不建目录。"""
    try:
        from wewrite.toolkit.theme import list_themes
        names = list_themes()
    except Exception:
        names = []
    return [{"id": n, "label": n} for n in sorted(set(names))]
