"""路径安全层：唯一数据根、逐级 openat + O_NOFOLLOW、产物白名单、资产登记复核。

设计 §6 不变式：
- ``paths.home()`` 是唯一数据根，子进程只在自身环境设固定 WEWRITE_HOME，不改 host 全局环境。
- runId 先正则 ``^[0-9]{8}-[0-9]{6}-[a-f0-9]{6}$`` 再校验 state.run_id。
- kind 映射固定文件名，不信任 state.artifacts 指向任意位置。
- 校验规范化后的真实路径在该 run 根内，拒绝符号链接、``..``、绝对路径、编码穿越、
  NUL、Windows 分隔符、跨 run 引用。
- 创建/打开阶段防 symlink 竞态（全程 dir_fd + O_NOFOLLOW），不做字符串 startsWith。

读面绝不建目录/锁文件；打开失败统一映射为业务错误（中文、无绝对路径回显）。
"""

from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass

from .errors import FacadeError, forbidden, invalid_input, not_found

RUN_ID_RE = re.compile(r"^[0-9]{8}-[0-9]{6}-[a-f0-9]{6}$")

# kind -> 固定文件名（与上游 create_run 的 artifacts 命名一致，见 wewrite.runs.create_run）。
KIND_FILES: dict[str, str] = {
    "brief": "brief.yaml",
    "claims": "claims.yaml",
    "draft": "draft.md",
    "article": "article.md",
    "review_report": "review-report.json",
    "sources": "sources.yaml",
    "illustrated_article": "article-illustrated.md",
    "image_prompts": "image-prompts.md",
    "images_manifest": "images.json",
    "preview": "preview.html",
}

KINDS = tuple(KIND_FILES)

# 上游 ensure_home 会建的标准子目录。
STANDARD_SUBDIRS = ("output", "runs", "exemplars", "corpus", "lessons", "themes", "personas")

_O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_O_BINARY = getattr(os, "O_BINARY", 0)

# 资产登记目录（插件增量，放在 run 根内，见设计 §6 存储布局）。
ASSET_REGISTRY_DIR = "assets"
ASSET_REGISTRY_FILE = "manifest.json"
ASSET_ROLES = ("cover", "inline")
ASSET_MEDIA_TYPES = ("image/png", "image/jpeg")

# 资产文件名只允许固定安全字符，由 facade 生成，不接受原始路径。
_ASSET_NAME_RE = re.compile(r"^[a-f0-9]{64}\.(png|jpg)$")
_REL_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


def home() -> str:
    """唯一数据根：$WEWRITE_HOME 或 ~/.wewrite。不保证存在。"""
    return os.path.abspath(os.environ.get("WEWRITE_HOME", "") or os.path.join(os.path.expanduser("~"), ".wewrite"))


def validate_run_id(run_id: str) -> str:
    """runId 严格正则校验；不合法抛 INVALID_INPUT（不泄露路径）。"""
    if not isinstance(run_id, str) or not RUN_ID_RE.fullmatch(run_id):
        raise invalid_input("runId 不合法")
    return run_id


def _os_reason(exc: OSError) -> str:
    if isinstance(exc, FileNotFoundError) or exc.errno == 2:
        return "enoent"
    if isinstance(exc, NotADirectoryError) or exc.errno == 20:
        return "enotdir"
    if getattr(exc, "errno", None) == 40:
        return "eloop"
    return "oserror"


@dataclass(frozen=True)
class HomeIdentity:
    dev: int
    ino: int


# 进程级状态根身份钉住缓存（同 wewrite-platform 口径：首次成功打开钉住，之后逐次比对）。
_home_identity_cache: dict[str, HomeIdentity] = {}


def _open_root() -> int:
    """重开状态根目录 fd（O_DIRECTORY|O_NOFOLLOW），首次钉住身份、之后比对。"""
    root = home()
    try:
        fd = os.open(root, os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW)
    except OSError as exc:
        raise forbidden(f"数据根不可访问（{_os_reason(exc)}）") from exc
    try:
        st = os.fstat(fd)
        if not stat.S_ISDIR(st.st_mode):
            raise forbidden("数据根不是目录")
        pinned = _home_identity_cache.get(root)
        if pinned is None:
            _home_identity_cache[root] = HomeIdentity(dev=st.st_dev, ino=st.st_ino)
        elif st.st_dev != pinned.dev or st.st_ino != pinned.ino:
            raise forbidden("数据根身份已变化")
    except BaseException:
        os.close(fd)
        raise
    return fd


def _open_dir_at(parent_fd: int, name: str, what: str) -> int:
    try:
        fd = os.open(name, os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW, dir_fd=parent_fd)
    except OSError as exc:
        raise forbidden(f"{what} 不可访问（{_os_reason(exc)}）") from exc
    try:
        st = os.fstat(fd)
        if not stat.S_ISDIR(st.st_mode):
            raise forbidden(f"{what} 不是目录")
    except BaseException:
        os.close(fd)
        raise
    return fd


def _open_run_dir_fd(run_id: str, *, create: bool = False) -> tuple[int, bool]:
    """打开 runs/<run_id> 目录 fd；返回 (fd, run_exists)。读面 create=False，不建目录。"""
    validate_run_id(run_id)
    root_fd = _open_root()
    try:
        try:
            runs_fd = _open_dir_at(root_fd, "runs", "runs")
        except FacadeError as exc:
            if exc.code == "FORBIDDEN":
                raise not_found("runs 目录不存在") from exc
            raise
        try:
            try:
                run_fd = _open_dir_at(runs_fd, run_id, "run")
                return run_fd, True
            except FacadeError as exc:
                if exc.code == "FORBIDDEN":
                    if create:
                        os.mkdir(run_id, dir_fd=runs_fd)
                        return _open_dir_at(runs_fd, run_id, "run"), True
                    raise not_found("run 不存在") from exc
                raise
        finally:
            os.close(runs_fd)
    finally:
        os.close(root_fd)


def open_artifact(run_id: str, kind: str):
    """安全打开 run 产物文件，返回 (fd, stat_result)。kind 固定白名单，不信任 state.artifacts。"""
    if kind not in KIND_FILES:
        raise not_found("产物类型不合法")
    validate_run_id(run_id)
    run_fd, _exists = _open_run_dir_fd(run_id)
    try:
        filename = KIND_FILES[kind]
        try:
            fd = os.open(filename, os.O_RDONLY | _O_NOFOLLOW | _O_BINARY, dir_fd=run_fd)
        except OSError as exc:
            reason = _os_reason(exc)
            if reason == "enoent":
                raise not_found("产物缺失") from exc
            raise forbidden(f"产物不可访问（{reason}）") from exc
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode):
                raise forbidden("产物不是普通文件")
            if st.st_nlink < 1:
                raise forbidden("产物已被删除")
            return fd, st
        except BaseException:
            os.close(fd)
            raise
    finally:
        os.close(run_fd)


def open_state(run_id: str):
    """安全打开 runs/<run_id>/state.yaml（读面，不升级、不写盘）。"""
    validate_run_id(run_id)
    run_fd, _exists = _open_run_dir_fd(run_id)
    try:
        try:
            fd = os.open("state.yaml", os.O_RDONLY | _O_NOFOLLOW | _O_BINARY, dir_fd=run_fd)
        except OSError as exc:
            raise not_found("state 不存在") from exc
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode):
                raise forbidden("state 不是普通文件")
            if st.st_nlink < 1:
                raise forbidden("state 已被删除")
            return fd, st
        except BaseException:
            os.close(fd)
            raise
    finally:
        os.close(run_fd)


def list_run_ids() -> list[str]:
    """安全枚举 runs/ 下合法 runId（读面；目录不存在返回空列表）。"""
    root = home()
    try:
        root_fd = os.open(root, os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW)
    except OSError:
        return []
    try:
        try:
            runs_fd = _open_dir_at(root_fd, "runs", "runs")
        except FacadeError:
            return []
        try:
            names = []
            with os.scandir(runs_fd) as it:
                for entry in it:
                    if entry.name.startswith("."):
                        continue
                    if entry.is_dir(follow_symlinks=False) and RUN_ID_RE.fullmatch(entry.name):
                        names.append(entry.name)
            return sorted(names)
        finally:
            os.close(runs_fd)
    finally:
        os.close(root_fd)


def open_run_file_rel(run_id: str, relpath: str):
    """安全打开 runs/<run_id>/<relpath>（资产用）。relpath 只允许安全段，逐段 openat。"""
    validate_run_id(run_id)
    if not isinstance(relpath, str) or not relpath or relpath.startswith("/") or relpath.startswith("\\"):
        raise forbidden("路径不合法")
    segments = relpath.split("/")
    if not segments or not all(_REL_SEGMENT_RE.fullmatch(s) for s in segments):
        raise forbidden("路径不合法")

    run_fd, _exists = _open_run_dir_fd(run_id)
    cur_fd = run_fd
    cur_is_run = True
    try:
        for segment in segments[:-1]:
            try:
                nxt = os.open(segment, os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW, dir_fd=cur_fd)
            except OSError as exc:
                raise forbidden(f"路径不可访问（{_os_reason(exc)}）") from exc
            if not cur_is_run:
                os.close(cur_fd)
            cur_fd = nxt
            cur_is_run = False
        try:
            fd = os.open(segments[-1], os.O_RDONLY | _O_NOFOLLOW | _O_BINARY, dir_fd=cur_fd)
        except OSError as exc:
            raise not_found("文件不存在") from exc
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            os.close(fd)
            raise forbidden("不是普通文件")
        if st.st_nlink < 1:
            os.close(fd)
            raise forbidden("文件已被删除")
        return fd, st
    finally:
        os.close(cur_fd)


def read_file_text(fd: int, size_limit: int | None = None) -> str:
    """从已打开 fd 读文本（size_limit 为字节上限，超限立即停止）。"""
    with os.fdopen(fd, "r", encoding="utf-8") as f:
        if size_limit is None:
            return f.read()
        data = f.read(size_limit + 1)
        if len(data.encode("utf-8")) > size_limit:
            raise invalid_input("内容超限")
        return data


def read_file_bytes(fd: int, size_limit: int | None = None) -> bytes:
    """从已打开 fd 读字节（size_limit 为字节上限）。"""
    with os.fdopen(fd, "rb") as f:
        if size_limit is None:
            return f.read()
        data = f.read(size_limit + 1)
        if len(data) > size_limit:
            raise invalid_input("内容超限")
        return data


def safe_relative_segments(relpath: str) -> list[str]:
    """校验并返回相对路径段；非法抛 INVALID_INPUT（不泄露路径）。"""
    if not isinstance(relpath, str) or not relpath or relpath.startswith("/") or relpath.startswith("\\"):
        raise invalid_input("路径不合法")
    if "\x00" in relpath:
        raise invalid_input("路径不合法")
    segments = relpath.split("/")
    if any(s in ("", ".", "..") for s in segments):
        raise invalid_input("路径不合法")
    if not all(_REL_SEGMENT_RE.fullmatch(s) for s in segments):
        raise invalid_input("路径不合法")
    return segments
