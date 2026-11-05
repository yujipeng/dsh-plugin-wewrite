"""learn / exemplar 账号级自学习持久层（方案 B 批一新增，不涉网络）。

- ``learn.record``：记录一次「用户改稿」差异（原文 + 改后 + diff 摘要 + 时间），落
  ``{home}/lessons/<date>-diff-<n>.yaml``；文件名由 facade 生成，不接受任意路径。
- ``learn.list``：列出已记录的改稿学习条目（只读，不建目录）。
- ``exemplar.put/list/get``：范文入库与列举，落 ``{home}/exemplars/<id>.md`` +
  ``{home}/exemplars/index.yaml``；id 只允许 ``^[a-f0-9]{16}$``，拒绝任意路径。
- 写面走 home 全局锁 + 原子写；带绑定上下文（Agent 写）一律 FORBIDDEN（账号级）。
- 时间一律经 ``_now_*`` 注入点（测试可 monkeypatch，不引入时间炸弹）。
"""

from __future__ import annotations

import os
import re
import stat
from typing import Any

import yaml

from . import paths, storage
from .errors import forbidden, invalid_input, not_found

LESSONS_DIR = "lessons"
EXEMPLARS_DIR = "exemplars"
EXEMPLAR_INDEX = "index.yaml"

# exemplar id：由 facade 生成（content 哈希前 16 hex），只允许该形状。
_EXEMPLAR_ID_RE = re.compile(r"^[a-f0-9]{16}$")
# lesson 文件名由 facade 生成：<date>-diff-<n>.yaml 或 <date>-diff.yaml。
_LESSON_FILE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-diff(-\d+)?\.yaml$")

_OWNERSHIP = ("user", "third_party", "unknown")
_AUTHENTICITY = ("user_edited", "user_authored", "published", "unverified")

_EXEMPLAR_MAX_BYTES = 1 * 1024 * 1024
_LESSON_MAX_BYTES = 1 * 1024 * 1024


def _now_iso() -> str:
    from datetime import datetime
    return datetime.now().isoformat()


def _now_date() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d")


def _open_root(*, create: bool) -> int:
    root = paths.home()
    if create:
        os.makedirs(root, exist_ok=True)
    return paths._open_root()


def _open_subdir(root_fd: int, name: str, *, create: bool) -> int | None:
    """打开 home 根下固定子目录 fd；create=True 时缺失则 mkdir。失败（读面）返回 None。"""
    try:
        return paths._open_dir_at(root_fd, name, name)
    except Exception:
        if not create:
            return None
        os.mkdir(name, dir_fd=root_fd)
        return paths._open_dir_at(root_fd, name, name)


def _assert_owner_write(payload: dict[str, Any]) -> None:
    """账号级写操作仅 owner 可改：携带 run 绑定上下文的 Agent 写路径一律拒绝。"""
    if payload.get("context"):
        raise forbidden("自学习与范文库为账号级配置，仅 owner 可改")


def _read_file_text_under(root_fd: int, name: str) -> str | None:
    """读 home 根 fd 下固定文件；不存在返回 None。"""
    try:
        fd = os.open(name, os.O_RDONLY | paths._O_NOFOLLOW | paths._O_BINARY, dir_fd=root_fd)
    except OSError:
        return None
    with os.fdopen(fd, "r", encoding="utf-8") as f:
        st = os.fstat(f.fileno())
        if not stat.S_ISREG(st.st_mode) or st.st_nlink < 1:
            raise forbidden("文件不是普通文件")
        return f.read()


def _require_plain_str(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise invalid_input(f"{field} 不合法")
    return value


# ── learn ────────────────────────────────────────────────────────────────────

def learn_record(payload: dict[str, Any]) -> dict[str, Any]:
    _assert_owner_write(payload)
    draft = _require_plain_str(payload.get("draft"), "draft")
    final = _require_plain_str(payload.get("final"), "final")
    diff_summary = payload.get("diffSummary")
    if not isinstance(diff_summary, dict):
        raise invalid_input("diffSummary 不合法")
    patterns = payload.get("patterns", [])
    if not isinstance(patterns, list):
        raise invalid_input("patterns 不合法")

    date = _now_date()
    data: dict[str, Any] = {
        "date": date,
        "timestamp": _now_iso(),
        "draft": draft,
        "final": final,
        "diff_summary": diff_summary,
        "patterns": patterns,
    }
    text = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
    raw = text.encode("utf-8")
    if len(raw) > _LESSON_MAX_BYTES:
        raise invalid_input("学习条目超过 1 MiB 上限")

    with storage.home_lock(create=True):
        root_fd = _open_root(create=True)
        try:
            lessons_fd = _open_subdir(root_fd, LESSONS_DIR, create=True)
            assert lessons_fd is not None
            try:
                filename = f"{date}-diff.yaml"
                counter = 1
                while True:
                    try:
                        os.stat(filename, dir_fd=lessons_fd, follow_symlinks=False)
                    except FileNotFoundError:
                        break
                    filename = f"{date}-diff-{counter}.yaml"
                    counter += 1
                storage._write_file_atomically(lessons_fd, filename, raw)
            finally:
                os.close(lessons_fd)
        finally:
            os.close(root_fd)

    return {"file": f"{LESSONS_DIR}/{filename}", "date": date, "version": storage.sha256_hex(raw)}


def learn_list() -> dict[str, Any]:
    """列出已记录的改稿学习条目（只读，不建目录/锁文件）。"""
    try:
        root_fd = _open_root(create=False)
    except Exception:
        return {"items": [], "total": 0}
    try:
        lessons_fd = _open_subdir(root_fd, LESSONS_DIR, create=False)
        if lessons_fd is None:
            return {"items": [], "total": 0}
        try:
            names: list[str] = []
            with os.scandir(lessons_fd) as it:
                for entry in it:
                    if entry.name.startswith("."):
                        continue
                    if _LESSON_FILE_RE.fullmatch(entry.name) and entry.is_file(follow_symlinks=False):
                        names.append(entry.name)
            names.sort(reverse=True)
            items: list[dict[str, Any]] = []
            for name in names:
                text = _read_file_text_under(lessons_fd, name)
                if text is None:
                    continue
                try:
                    data = yaml.safe_load(text)
                except yaml.YAMLError:
                    data = None
                summary: dict[str, Any] = {"file": f"{LESSONS_DIR}/{name}"}
                if isinstance(data, dict):
                    summary["date"] = data.get("date")
                    summary["timestamp"] = data.get("timestamp")
                    summary["diffSummary"] = data.get("diff_summary")
                    summary["patternCount"] = len(data.get("patterns") or [])
                items.append(summary)
            return {"items": items, "total": len(items)}
        finally:
            os.close(lessons_fd)
    finally:
        os.close(root_fd)


# ── exemplar ────────────────────────────────────────────────────────────────

def _normalize_exemplar(payload: dict[str, Any]) -> dict[str, Any]:
    title = _require_plain_str(payload.get("title"), "title")
    content = payload.get("content")
    if not isinstance(content, str):
        raise invalid_input("content 不合法")
    source = payload.get("source")
    if source is None:
        source = title
    source = _require_plain_str(source, "source")
    category = payload.get("category")
    if category is None:
        category = "general"
    if not isinstance(category, str) or not category.strip():
        raise invalid_input("category 不合法")
    ownership = payload.get("ownership", "unknown")
    if ownership not in _OWNERSHIP:
        raise invalid_input("ownership 不合法")
    authenticity = payload.get("authenticity", "unverified")
    if authenticity not in _AUTHENTICITY:
        raise invalid_input("authenticity 不合法")
    return {
        "title": title,
        "source": source,
        "category": category,
        "content": content,
        "ownership": ownership,
        "authenticity": authenticity,
    }


def exemplar_put(payload: dict[str, Any]) -> dict[str, Any]:
    _assert_owner_write(payload)
    ex = _normalize_exemplar(payload)
    content_raw = ex["content"].encode("utf-8")
    if len(content_raw) > _EXEMPLAR_MAX_BYTES:
        raise invalid_input("范文超过 1 MiB 上限")
    exemplar_id = storage.sha256_hex(content_raw)[:16]

    frontmatter = {
        "title": ex["title"],
        "source": ex["source"],
        "category": ex["category"],
        "ownership": ex["ownership"],
        "authenticity": ex["authenticity"],
        "allowed_uses": ["style", "structure"],
        "personal_materials_reusable": False,
        "exemplar_id": exemplar_id,
    }
    file_text = (
        "---\n"
        + yaml.safe_dump(frontmatter, allow_unicode=True, sort_keys=False)
        + "---\n\n"
        + ex["content"]
    )
    file_raw = file_text.encode("utf-8")

    with storage.home_lock(create=True):
        root_fd = _open_root(create=True)
        try:
            ex_fd = _open_subdir(root_fd, EXEMPLARS_DIR, create=True)
            assert ex_fd is not None
            try:
                storage._write_file_atomically(ex_fd, f"{exemplar_id}.md", file_raw)
                _upsert_index(ex_fd, exemplar_id, frontmatter)
            finally:
                os.close(ex_fd)
        finally:
            os.close(root_fd)

    return {
        "id": exemplar_id,
        "title": ex["title"],
        "source": ex["source"],
        "category": ex["category"],
        "ownership": ex["ownership"],
        "authenticity": ex["authenticity"],
        "version": storage.sha256_hex(file_raw),
    }


def _load_index(ex_fd: int) -> list[dict[str, Any]]:
    text = _read_file_text_under(ex_fd, EXEMPLAR_INDEX)
    if text is None:
        return []
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return []
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def _upsert_index(ex_fd: int, exemplar_id: str, frontmatter: dict[str, Any]) -> None:
    index = _load_index(ex_fd)
    entry = {
        "id": exemplar_id,
        "title": frontmatter.get("title"),
        "source": frontmatter.get("source"),
        "category": frontmatter.get("category"),
        "ownership": frontmatter.get("ownership"),
        "authenticity": frontmatter.get("authenticity"),
    }
    index = [item for item in index if item.get("id") != exemplar_id]
    index.append(entry)
    index.sort(key=lambda x: (x.get("category", ""), x.get("title", "")))
    text = yaml.safe_dump(index, allow_unicode=True, sort_keys=False)
    storage._write_file_atomically(ex_fd, EXEMPLAR_INDEX, text.encode("utf-8"))


def exemplar_list() -> dict[str, Any]:
    try:
        root_fd = _open_root(create=False)
    except Exception:
        return {"items": [], "total": 0}
    try:
        ex_fd = _open_subdir(root_fd, EXEMPLARS_DIR, create=False)
        if ex_fd is None:
            return {"items": [], "total": 0}
        try:
            items = _load_index(ex_fd)
            return {"items": items, "total": len(items)}
        finally:
            os.close(ex_fd)
    finally:
        os.close(root_fd)


def exemplar_get(payload: dict[str, Any]) -> dict[str, Any]:
    exemplar_id = payload.get("id")
    if not isinstance(exemplar_id, str) or not _EXEMPLAR_ID_RE.fullmatch(exemplar_id):
        raise invalid_input("id 不合法")
    try:
        root_fd = _open_root(create=False)
    except Exception:
        raise not_found("范文不存在")
    try:
        ex_fd = _open_subdir(root_fd, EXEMPLARS_DIR, create=False)
        if ex_fd is None:
            raise not_found("范文不存在")
        try:
            text = _read_file_text_under(ex_fd, f"{exemplar_id}.md")
            if text is None:
                raise not_found("范文不存在")
        finally:
            os.close(ex_fd)
    finally:
        os.close(root_fd)

    # 拆 frontmatter 与正文。
    m = re.match(r"^---\r?\n([\s\S]*?)\r?\n---\r?\n([\s\S]*)$", text)
    if not m:
        raise invalid_input("范文格式不合法")
    try:
        meta = yaml.safe_load(m.group(1))
    except yaml.YAMLError as exc:
        raise invalid_input("范文格式不合法") from exc
    if not isinstance(meta, dict):
        raise invalid_input("范文格式不合法")
    return {
        "id": exemplar_id,
        "title": meta.get("title"),
        "source": meta.get("source"),
        "category": meta.get("category"),
        "ownership": meta.get("ownership", "unknown"),
        "authenticity": meta.get("authenticity", "unverified"),
        "allowed_uses": meta.get("allowed_uses", ["style", "structure"]),
        "personal_materials_reusable": meta.get("personal_materials_reusable", False),
        "content": m.group(2),
    }
