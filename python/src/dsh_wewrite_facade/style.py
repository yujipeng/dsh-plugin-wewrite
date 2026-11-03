"""style.yaml 账号级写作风格配置的受控读写（方案 B 批一新增）。

- ``style.yaml`` 位于 ``{home}`` 根，是选题/写作环节的真实输入（wewrite-topic 会读
  topics / target_audience / blacklist / content_style）。
- 读面绝不建目录/锁文件；不存在返回 NOT_FOUND（业务失败），不报 INTERNAL。
- 写面走 home 全局锁 + 同目录临时文件 + replace 原子写；写入前把旧版本备份为
  ``style.yaml.bak``。
- schema 严格校验（拒绝未知字段），错误中文且带明确字段名。
- ``style.put`` 属账号级配置，仅 owner 可改：携带 run/generation 绑定上下文
  （context）的 Agent 写路径一律 FORBIDDEN。
"""

from __future__ import annotations

import os
import stat
from typing import Any

import yaml

from . import paths, storage
from .errors import forbidden, invalid_input, not_found

STYLE_FILENAME = "style.yaml"
STYLE_BACKUP_FILENAME = "style.yaml.bak"

# 允许字段 → 期望类型。blacklist 是联合结构（字符串数组或 {words,topics} 对象），单独校验。
_STYLE_FIELDS: dict[str, str] = {
    "name": "str",
    "industry": "str",
    "topics": "list[str]",
    "tone": "str",
    "theme": "str",
    "target_audience": "str",
    "voice": "str",
    "writing_persona": "str",
    "word_count": "str",
    "reference_accounts": "list[str]",
    "cover_style": "str",
    "cover_template": "str",
    "author": "str",
    "content_style": "str",
}

_REQUIRED_FIELDS = ("name", "industry", "topics", "tone", "theme")

# 范文/学习条目文件名安全字符（不接受任意路径）。
_EXEMPLAR_ID_RE = r"^[a-z0-9-]+$"

_MAX_STYLE_BYTES = 1 * 1024 * 1024  # 1 MiB 上限，防配置炸弹。


def _open_root(*, create: bool) -> int:
    """打开 home 根目录 fd（O_DIRECTORY|O_NOFOLLOW）。create=True 时先 makedirs。"""
    root = paths.home()
    if create:
        os.makedirs(root, exist_ok=True)
    return paths._open_root()


def _read_home_file(name: str) -> bytes | None:
    """安全读 home 根下固定文件；不存在返回 None。读面不建目录/锁文件。"""
    try:
        root_fd = _open_root(create=False)
    except Exception:
        return None
    try:
        try:
            fd = os.open(name, os.O_RDONLY | paths._O_NOFOLLOW | paths._O_BINARY, dir_fd=root_fd)
        except OSError:
            return None
        with os.fdopen(fd, "rb") as f:
            st = os.fstat(f.fileno())
            if not stat.S_ISREG(st.st_mode) or st.st_nlink < 1:
                raise forbidden("配置不是普通文件")
            return f.read()
    finally:
        os.close(root_fd)


def _write_home_file_atomic(name: str, data: bytes) -> None:
    """home 根下原子写（同目录临时文件 + replace）。调用方须已持 home 锁。"""
    root_fd = _open_root(create=True)
    try:
        storage._write_file_atomically(root_fd, name, data)
    finally:
        os.close(root_fd)


def _backup_style(root_fd: int) -> None:
    """把当前 style.yaml 备份为 style.yaml.bak（写入前调用，无旧版则跳过）。

    只把「确实不存在」（ENOENT）当无旧版；存在但不可读 / 是 symlink（ELOOP，O_NOFOLLOW 命中）
    / 权限错等一律 fail-closed——不得静默跳过备份、更不得覆盖一个 symlink 目录项。
    """
    try:
        fd = os.open(STYLE_FILENAME, os.O_RDONLY | paths._O_NOFOLLOW | paths._O_BINARY, dir_fd=root_fd)
    except FileNotFoundError:
        return
    except OSError as exc:
        if getattr(exc, "errno", None) == 2:  # ENOENT 也统一按不存在处理（双保险）。
            return
        raise forbidden(f"style.yaml 不是普通文件，拒绝写入（{paths._os_reason(exc)}）") from exc
    with os.fdopen(fd, "rb") as f:
        st = os.fstat(f.fileno())
        if not stat.S_ISREG(st.st_mode) or st.st_nlink < 1:
            raise forbidden("style.yaml 不是普通文件，拒绝写入")
        old = f.read()
    storage._write_file_atomically(root_fd, STYLE_BACKUP_FILENAME, old)


def _is_str_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _validate_blacklist(value: Any) -> str | None:
    """校验 blacklist 联合结构；返回中文问题或 None。"""
    if isinstance(value, list):
        if all(isinstance(item, str) for item in value):
            return None
        return "blacklist 含非字符串项"
    if isinstance(value, dict):
        # 对象形式 {words: [...], topics: [...]}（上游 style-template.md）。
        for key in ("words", "topics"):
            item = value.get(key)
            if item is None:
                continue
            if not isinstance(item, list) or not all(isinstance(x, str) for x in item):
                return f"blacklist.{key} 含非字符串项"
        unknown = set(value.keys()) - {"words", "topics"}
        if unknown:
            return f"blacklist 未知字段: {', '.join(sorted(unknown))}"
        return None
    return "blacklist 必须是字符串数组或 {words, topics} 对象"


def validate_style(data: Any) -> list[str]:
    """严格校验 style 数据，返回中文问题清单（带字段名）；合法时为空列表。"""
    issues: list[str] = []
    if not isinstance(data, dict):
        return ["style 必须是对象"]
    for key in data:
        if key not in _STYLE_FIELDS and key != "blacklist":
            issues.append(f"未知字段: {key}")
    for field in _REQUIRED_FIELDS:
        if field not in data:
            issues.append(f"缺少必填字段: {field}")
            continue
        value = data[field]
        if field == "topics":
            if not isinstance(value, list):
                issues.append("topics 必须是数组")
            elif not all(isinstance(item, str) for item in value):
                issues.append("topics 含非字符串项")
        else:
            if not isinstance(value, str):
                issues.append(f"{field} 必须是字符串")
    # 可选字段类型。
    for field, expected in _STYLE_FIELDS.items():
        if field not in data or data[field] is None:
            continue
        value = data[field]
        if expected == "str":
            if not isinstance(value, str):
                issues.append(f"{field} 必须是字符串")
        elif expected == "list[str]":
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                issues.append(f"{field} 必须是字符串数组")
    if "blacklist" in data:
        problem = _validate_blacklist(data["blacklist"])
        if problem:
            issues.append(problem)
    return issues


def _normalize_style(data: dict[str, Any]) -> dict[str, Any]:
    """按固定字段顺序归一化（只保留允许字段），供写盘与结构化返回。"""
    order = ["name", "industry", "target_audience", "topics", "tone", "voice",
             "word_count", "content_style", "writing_persona", "blacklist",
             "reference_accounts", "theme", "cover_style", "cover_template", "author"]
    out: dict[str, Any] = {}
    for key in order:
        if key in data and data[key] is not None:
            out[key] = data[key]
    return out


def read_style() -> dict[str, Any] | None:
    """读 style.yaml 并解析；不存在或不可读返回 None（读面不写盘）。"""
    raw = _read_home_file(STYLE_FILENAME)
    if raw is None:
        return None
    try:
        data = yaml.safe_load(raw.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def style_get() -> dict[str, Any]:
    """style.get：读结构化字段与原文；不存在返回 NOT_FOUND。"""
    raw = _read_home_file(STYLE_FILENAME)
    if raw is None:
        raise not_found("style.yaml 不存在")
    try:
        text = raw.decode("utf-8")
        data = yaml.safe_load(text)
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise invalid_input("style.yaml 不是合法 YAML") from exc
    if not isinstance(data, dict):
        raise invalid_input("style.yaml 必须是 YAML 对象")
    return {
        "exists": True,
        "style": _normalize_style(data),
        "raw": text,
        "version": storage.sha256_hex(raw),
    }


def style_put(payload: dict[str, Any]) -> dict[str, Any]:
    """style.put：校验 → 备份 → 原子写。带绑定上下文（Agent 写）返回 FORBIDDEN。"""
    # 权限边界：账号级配置仅 owner 可改（owner 经浏览器 RPC，不携带 run 绑定上下文）。
    if payload.get("context"):
        raise forbidden("写作风格为账号级配置，仅 owner 可改")

    style_data = payload.get("style")
    issues = validate_style(style_data)
    if issues:
        raise invalid_input("；".join(issues))

    normalized = _normalize_style(style_data)
    text = yaml.safe_dump(normalized, allow_unicode=True, sort_keys=False)
    raw = text.encode("utf-8")
    if len(raw) > _MAX_STYLE_BYTES:
        raise invalid_input("style 配置超过 1 MiB 上限")

    with storage.home_lock(create=True):
        root_fd = _open_root(create=True)
        try:
            _backup_style(root_fd)
            storage._write_file_atomically(root_fd, STYLE_FILENAME, raw)
        finally:
            os.close(root_fd)

    return {"exists": True, "style": normalized, "version": storage.sha256_hex(raw), "backup": STYLE_BACKUP_FILENAME}


def style_validate(payload: dict[str, Any]) -> dict[str, Any]:
    """style.validate：只校验不写，返回问题清单。"""
    issues = validate_style(payload.get("style"))
    return {"valid": len(issues) == 0, "issues": issues}
