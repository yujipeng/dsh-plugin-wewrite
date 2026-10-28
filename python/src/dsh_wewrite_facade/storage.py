"""写面与并发（设计 §6）：fcntl.flock 固定顺序、原子写、版本 CAS、可恢复 journal。

锁顺序固定：先 home 全局锁（仅创建/历史/共享索引），再 run 锁；普通 run 文件修改
只取 run 锁，禁止反序。内容写用同目录临时文件 + replace；版本用实际文件字节 SHA-256
（不用 mtime）；锁内校验 expectedVersion。

读面绝不写盘：GET 不创建锁文件；缺失时返回需初始化诊断。锁文件由首次显式写操作预建。
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import secrets
import stat
import tempfile
import threading
from contextlib import contextmanager
from typing import Any, Iterator

import yaml

from . import paths
from .errors import FacadeError, forbidden, internal, invalid_input, not_found, state_corrupted, version_conflict

_HOME_LOCK_NAME = ".dsh-workbench.lock"
_RUN_LOCK_NAME = ".dsh-workbench.lock"

# 锁顺序记账（线程内）：固定顺序 home → run；run → home 反序在获取 home 前被拒绝。
# 每个 facade 子进程单请求单响应，跨进程不出现同一线程同时持两把锁的 run→home 反序。
_lock_ctx = threading.local()


def _lock_stack() -> list[str]:
    stack = getattr(_lock_ctx, "stack", None)
    if stack is None:
        stack = []
        _lock_ctx.stack = stack
    return stack

# journal 目录（插件增量，放在 run 根内）
_JOURNAL_DIR = ".dsh-workbench"
_JOURNAL_FILE = "journal.json"


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def ensure_writable_state(run_id: str) -> None:
    """统一写面守卫：在 run_lock 内、任何写入前校验**原始** state.yaml 可读性。

    直接解析 state.yaml 原始字节（不经过 projection 的合成状态），损坏即抛
    STATE_CORRUPTED（明确业务码，前端可区分「状态不可读」与真实内部故障），
    绝不写回合成状态、绝不覆盖损坏证据。
    """
    paths.validate_run_id(run_id)
    try:
        fd, _st = paths.open_state(run_id)
    except FacadeError as exc:
        if exc.code == "NOT_FOUND":
            # 写操作遇到 state 不存在：不可读，拒绝（不给 INTERNAL）。
            raise state_corrupted("状态不可读，禁止编辑") from exc
        raise
    try:
        with os.fdopen(fd, "rb") as f:
            raw = f.read()
    except OSError as exc:
        raise state_corrupted("状态不可读，禁止编辑") from exc
    try:
        parsed = yaml.safe_load(raw.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise state_corrupted("状态不可读，禁止编辑") from exc
    if not isinstance(parsed, dict):
        raise state_corrupted("状态不可读，禁止编辑")
    if parsed.get("run_id") != run_id:
        raise forbidden("state 与 runId 不符")
    if parsed.get("status") == "unreadable":
        raise state_corrupted("状态不可读，禁止编辑")


def _open_lock_file(path_name: str, parent: str, *, create: bool) -> int:
    """打开锁文件 fd。create=False 绝不创建（读面）；create=True 时创建并保持。"""
    flags = os.O_RDWR if create else os.O_RDONLY
    if create:
        flags |= os.O_CREAT
    parent_fd = os.open(parent, os.O_RDONLY | paths._O_DIRECTORY | paths._O_NOFOLLOW)
    try:
        return os.open(path_name, flags | paths._O_NOFOLLOW, 0o600, dir_fd=parent_fd)
    finally:
        os.close(parent_fd)


@contextmanager
def home_lock(*, create: bool) -> Iterator[None]:
    """home 全局写锁（仅创建/历史/共享索引）。读面 create=False 不建锁文件。

    锁顺序：home 必须最先获取（栈为空时才能取 home），拒绝 run→home 反序。
    """
    stack = _lock_stack()
    if "run" in stack:
        # 反序获取：先持 run 再取 home，违反固定顺序。
        raise forbidden("锁顺序违规：不能先持 run 锁再取全局锁")
    root = paths.home()
    if create:
        # 写路径允许预建数据根（create_run 的副作用清单含 home 目录）。
        os.makedirs(root, exist_ok=True)
    try:
        fd = _open_lock_file(_HOME_LOCK_NAME, root, create=create)
    except FileNotFoundError:
        raise FacadeError("CAPABILITY_UNAVAILABLE", "数据根未初始化，需先创建 run")
    except OSError as exc:
        raise forbidden(f"全局锁不可用（{paths._os_reason(exc)}）") from exc
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        stack.append("home")
        try:
            yield
        finally:
            stack.pop()
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


@contextmanager
def run_lock(run_id: str, *, create: bool) -> Iterator[None]:
    """run 锁（普通 run 文件修改）。读面 create=False 不建锁文件。

    锁顺序：run 锁可在 home 锁之后获取（home 栈顶），也可独立获取；一旦先持 run
    再请求 home 会被 home_lock 拒绝。
    """
    paths.validate_run_id(run_id)
    run_fd, _exists = paths._open_run_dir_fd(run_id, create=False)
    try:
        try:
            fd = os.open(_RUN_LOCK_NAME, (os.O_RDWR if create else os.O_RDONLY) | paths._O_NOFOLLOW
                         | (os.O_CREAT if create else 0), 0o600, dir_fd=run_fd)
        except OSError as exc:
            raise FacadeError("CAPABILITY_UNAVAILABLE", "run 锁不可用，需先创建 run") from exc
    finally:
        os.close(run_fd)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        stack = _lock_stack()
        stack.append("run")
        try:
            yield
        finally:
            stack.pop()
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _write_file_atomically(dir_fd: int, filename: str, data: bytes, mode: int = 0o600) -> None:
    """同目录临时文件 + fsync + replace + 目录 fsync（全程 dir_fd）。"""
    tmp = f".{filename}.{secrets.token_hex(6)}.tmp"
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | paths._O_NOFOLLOW, mode, dir_fd=dir_fd)
    except OSError as exc:
        raise forbidden(f"写入失败（{paths._os_reason(exc)}）") from exc
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, filename, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        os.fsync(dir_fd)
    except BaseException:
        try:
            os.unlink(tmp, dir_fd=dir_fd)
        except OSError:
            pass
        raise


def _open_run_file_for_write(run_id: str, filename: str) -> int:
    """打开 run 根内文件的父目录 fd（run 根）。"""
    run_fd, _exists = paths._open_run_dir_fd(run_id, create=False)
    return run_fd


def ensure_run_lock_file(run_id: str) -> None:
    """首次显式写操作预建 run 锁文件（create_run 后调用，使后续 create=False 写操作可用）。"""
    paths.validate_run_id(run_id)
    run_fd, _exists = paths._open_run_dir_fd(run_id, create=False)
    try:
        fd = os.open(_RUN_LOCK_NAME, os.O_RDWR | os.O_CREAT | paths._O_NOFOLLOW, 0o600, dir_fd=run_fd)
    except OSError as exc:
        raise forbidden(f"run 锁不可用（{paths._os_reason(exc)}）") from exc
    finally:
        os.close(run_fd)
    os.close(fd)


def write_run_file(run_id: str, filename: str, data: bytes) -> None:
    """原子写 runs/<run_id>/<filename>（调用方须已持 run 锁）。"""
    if not filename or filename.startswith(".") or "/" in filename or "\\" in filename or filename == "state.yaml":
        raise forbidden("文件名不合法")
    run_fd = _open_run_file_for_write(run_id, filename)
    try:
        _write_file_atomically(run_fd, filename, data)
    finally:
        os.close(run_fd)


def write_run_file_rel(run_id: str, relpath: str, data: bytes) -> None:
    """原子写 runs/<run_id>/<relpath>（调用方须已持 run 锁）。中间目录段缺失时创建。

    逐级 O_NOFOLLOW 打开，任一段是 symlink/非目录拒绝；仅 ENOENT 时创建目录。
    """
    segs = paths.safe_relative_segments(relpath)
    run_fd = _open_run_file_for_write(run_id, relpath)
    cur_fd = run_fd
    cur_is_run = True
    try:
        for segment in segs[:-1]:
            try:
                nxt = paths._open_dir_at(cur_fd, segment, "dir")
            except FacadeError as exc:
                if exc.code != "FORBIDDEN":
                    raise
                # 仅缺失（enoent）才创建目录；symlink/非目录等 O_NOFOLLOW 拒绝后仍会拒绝。
                try:
                    os.mkdir(segment, dir_fd=cur_fd)
                except FileExistsError:
                    # 已存在但打开失败（如 symlink）：重开仍会 O_NOFOLLOW 拒绝。
                    pass
                except OSError as e:
                    raise forbidden(f"目录创建失败（{paths._os_reason(e)}）") from e
                nxt = paths._open_dir_at(cur_fd, segment, "dir")
            if not cur_is_run:
                os.close(cur_fd)
            cur_fd = nxt
            cur_is_run = False
        _write_file_atomically(cur_fd, segs[-1], data)
    finally:
        if not cur_is_run:
            os.close(cur_fd)
        else:
            os.close(run_fd)


def write_state_yaml(run_id: str, text: str) -> None:
    run_fd = _open_run_file_for_write(run_id, "state.yaml")
    try:
        _write_file_atomically(run_fd, "state.yaml", text.encode("utf-8"))
    finally:
        os.close(run_fd)


def _journal_path(run_id: str, *, create: bool) -> tuple[int, str]:
    """打开（或创建）journal 所在目录 .dsh-workbench，返回 (dir_fd, journal_filename)。

    create=False（读面）绝不创建目录/文件。
    """
    run_fd, _exists = paths._open_run_dir_fd(run_id, create=False)
    try:
        try:
            jd_fd = paths._open_dir_at(run_fd, _JOURNAL_DIR, "journal")
        except FacadeError as exc:
            if not create:
                raise
            os.mkdir(_JOURNAL_DIR, dir_fd=run_fd)
            jd_fd = paths._open_dir_at(run_fd, _JOURNAL_DIR, "journal")
    finally:
        os.close(run_fd)
    return jd_fd, _JOURNAL_FILE


def read_journal(run_id: str) -> dict[str, Any]:
    """读 journal；不存在返回空。半提交事务由调用方判定为冲突。

    读面绝不创建 .dsh-workbench 目录或锁文件。
    """
    try:
        jd_fd, jname = _journal_path(run_id, create=False)
    except FacadeError:
        return {}
    try:
        try:
            fd = os.open(jname, os.O_RDONLY | paths._O_NOFOLLOW, dir_fd=jd_fd)
        except OSError:
            return {}
        with os.fdopen(fd, "rb") as f:
            raw = f.read()
    finally:
        os.close(jd_fd)
    try:
        data = json.loads(raw.decode("utf-8"))
        return data if isinstance(data, dict) else {}
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}


def write_journal(run_id: str, data: dict[str, Any]) -> None:
    jd_fd, jname = _journal_path(run_id, create=True)
    try:
        _write_file_atomically(jd_fd, jname, json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"))
    finally:
        os.close(jd_fd)


def _transaction_begin(run_id: str, txn_id: str, kind: str, expected: str | None) -> None:
    journal = read_journal(run_id)
    journal["pending"] = {"id": txn_id, "kind": kind, "expected": expected}
    write_journal(run_id, journal)


def _transaction_commit(run_id: str) -> None:
    journal = read_journal(run_id)
    journal.pop("pending", None)
    write_journal(run_id, journal)


def check_no_pending_transaction(run_id: str) -> None:
    """读面遇未完成事务报冲突，不展示半提交 pass。"""
    journal = read_journal(run_id)
    if journal.get("pending"):
        raise version_conflict("存在未完成事务，请稍后重试")


def article_save(
    run_id: str,
    markdown: str,
    expected_version: str | None,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    """锁内 CAS 保存正文（article.md）。同版 100 次并发最多一个成功，其余 VERSION_CONFLICT。"""
    paths.validate_run_id(run_id)
    data = markdown.encode("utf-8")

    with run_lock(run_id, create=True):
        ensure_writable_state(run_id)
        try:
            fd, _st = paths.open_artifact(run_id, "article")
        except FacadeError as exc:
            if exc.code == "NOT_FOUND":
                current = None
            else:
                raise
        else:
            current = sha256_hex(os.fdopen(fd, "rb").read())

        if current != expected_version:
            raise version_conflict("内容版本冲突，请刷新后重试")

        # 可恢复 journal：先写 pending，再写正文，再写 state 元数据，最后提交。
        txn_id = secrets.token_hex(8)
        _transaction_begin(run_id, txn_id, "article_save", expected_version)
        try:
            write_run_file(run_id, "article.md", data)
            new_version = sha256_hex(data)

            # 正文变化立即使旧审稿失效：移除 review_report（若存在，交由上层在锁内处理），
            # 这里通过在 journal 记录 invalidation，由上层一并处理。
            _invalidate_review(run_id)

            # 更新 state.yaml 的 metadata（标题/digest/themeId）。
            _update_state_metadata(run_id, metadata)
            _transaction_commit(run_id)
        except BaseException:
            raise

        return {
            "runId": run_id,
            "version": new_version,
            "reviewRequired": True,
            "previewReady": False,
        }


def _invalidate_review(run_id: str) -> None:
    """正文保存后旧审稿失效：删除 review-report.json（若存在）。"""
    try:
        run_fd, _exists = paths._open_run_dir_fd(run_id, create=False)
    except FacadeError:
        return
    try:
        try:
            os.unlink("review-report.json", dir_fd=run_fd)
        except FileNotFoundError:
            pass
    finally:
        os.close(run_fd)


def _update_state_metadata(run_id: str, metadata: dict[str, Any]) -> None:
    """更新 state.yaml 的 editorial 元数据（标题/digest/themeId）。

    先经写面守卫校验原始 state.yaml 可读，损坏即拒绝——绝不把 projection 的
    合成状态写回磁盘（那是抹除损坏证据的根因）。
    """
    ensure_writable_state(run_id)
    from . import projection
    state, _diags = projection.read_state(run_id)
    if "STATE_CORRUPTED" in _diags:
        raise state_corrupted("状态不可读，禁止编辑")
    editorial = dict(state.get("editorial") or {})
    title = metadata.get("title")
    if title:
        editorial["title"] = title
    digest = metadata.get("digest")
    if digest:
        editorial["digest"] = digest
    if metadata.get("themeId"):
        editorial["theme"] = metadata["themeId"]
    state["editorial"] = editorial
    state["updated"] = projection._now_rfc3339()
    write_state_yaml(run_id, yaml.safe_dump(state, allow_unicode=True, sort_keys=False))


def create_run(*, topic: str, mode: str, visual_mode: str, theme_id: str, request_id: str) -> dict[str, Any]:
    """调用上游 create_run（写面，持 home 全局锁）。副作用清单由调用方与测试逐项断言。

    上游 create_run 会：ensure_home()（建 home + 七个标准目录）、建 run 目录、
    写 state.yaml、写 sources.yaml、写 current_run 指针。
    """
    from wewrite.runs import create_run as _upstream_create

    with home_lock(create=True):
        state = _upstream_create(
            topic=topic,
            mode=mode,
            visual_mode=visual_mode,
            max_images=4,
            max_image_cost=None,
            allow_publish=False,
        )
    run_id = state.get("run_id")
    if not run_id:
        raise internal("创建 run 失败")
    # 首次显式写操作预建 run 锁文件，使后续 draft.prepare/asset.put 等写操作
    # 能以 create=False 取得 run 锁（读面仍不创建锁设施）。
    ensure_run_lock_file(run_id)
    return {"runId": run_id, "phase": "created"}


def create_run_side_effects() -> list[str]:
    """create_run 的写副作用清单（供测试与文档逐项断言）。"""
    return [
        "home 目录（若不存在）",
        "output/",
        "runs/",
        "exemplars/",
        "corpus/",
        "lessons/",
        "themes/",
        "personas/",
        "runs/<runId>/（run 目录）",
        "runs/<runId>/state.yaml",
        "runs/<runId>/sources.yaml",
        "current_run",
    ]


def _read_article(run_id: str) -> tuple[str, str]:
    fd, _st = paths.open_artifact(run_id, "article")
    data = os.fdopen(fd, "rb").read()
    return data.decode("utf-8"), sha256_hex(data)


def _current_run_bytes() -> bytes | None:
    """读取 current_run 指针原始字节；不存在返回 None。"""
    root_fd = paths._open_root()
    try:
        try:
            fd = os.open("current_run", os.O_RDONLY | paths._O_NOFOLLOW, dir_fd=root_fd)
        except FileNotFoundError:
            return None
        with os.fdopen(fd, "rb") as f:
            return f.read()
    finally:
        os.close(root_fd)


def _restore_current_run(content: bytes | None) -> None:
    """恢复 current_run 指针内容（fork 失败回滚用）。"""
    root_fd = paths._open_root()
    try:
        if content is None:
            try:
                os.unlink("current_run", dir_fd=root_fd)
            except FileNotFoundError:
                pass
        else:
            _write_file_atomically(root_fd, "current_run", content)
    finally:
        os.close(root_fd)


def _remove_run_dir(run_id: str) -> None:
    """回滚用：删除刚创建的 run 目录（仅限 fork 失败回滚）。

    只删除本 facade/上游 create_run 会写入的已知文件，不递归删除任意内容。
    """
    paths.validate_run_id(run_id)
    root_fd = paths._open_root()
    try:
        try:
            runs_fd = paths._open_dir_at(root_fd, "runs", "runs")
        except FacadeError:
            return
        try:
            try:
                run_fd = paths._open_dir_at(runs_fd, run_id, "run")
            except FacadeError:
                return
            try:
                for name in (".dsh-workbench.lock", "article.md", "state.yaml",
                             "sources.yaml", "review-report.json"):
                    try:
                        os.unlink(name, dir_fd=run_fd)
                    except FileNotFoundError:
                        pass
                    except OSError:
                        pass
                # 删除 .dsh-workbench 子目录（journal/intent）。
                try:
                    jd_fd = paths._open_dir_at(run_fd, _JOURNAL_DIR, "journal")
                except FacadeError:
                    pass
                else:
                    try:
                        for name in ("journal.json", "intent.json"):
                            try:
                                os.unlink(name, dir_fd=jd_fd)
                            except FileNotFoundError:
                                pass
                            except OSError:
                                pass
                    finally:
                        os.close(jd_fd)
                    try:
                        os.rmdir(_JOURNAL_DIR, dir_fd=run_fd)
                    except OSError:
                        pass
            finally:
                os.close(run_fd)
            try:
                os.rmdir(run_id, dir_fd=runs_fd)
            except OSError:
                pass
        finally:
            os.close(runs_fd)
    finally:
        os.close(root_fd)


def _rollback_fork(new_run_id: str | None, runs_before: set[str], current_ptr_before: bytes | None) -> None:
    """fork 失败回滚：删除新建空壳 run 目录，恢复 current_run 指针。"""
    try:
        if new_run_id is not None:
            _remove_run_dir(new_run_id)
        else:
            for rid in set(paths.list_run_ids()) - runs_before:
                _remove_run_dir(rid)
    finally:
        _restore_current_run(current_ptr_before)


def fork_article(run_id: str, expected_version: str | None) -> dict[str, Any]:
    """article/fork：复制源 run 正文到新 run。源 run 原文字节不变，parentRunId 可回溯。

    - 新 run 建好后立即预建 run 锁文件（ensure_run_lock_file），后续 run_lock(create=False) 可用。
    - 任何失败路径回滚：删除新建空壳 run 目录、恢复 current_run 指针逐字节不变。
    """
    from wewrite.runs import create_run as _upstream_create

    paths.validate_run_id(run_id)
    # 读源正文（run 锁内保证一致性快照 + 写面守卫校验源 run 可写）。
    with run_lock(run_id, create=False):
        ensure_writable_state(run_id)
        content, version = _read_article(run_id)
        if expected_version is not None and version != expected_version:
            raise version_conflict("内容版本冲突，请刷新后重试")

    runs_before = set(paths.list_run_ids())
    current_ptr_before = _current_run_bytes()

    new_run_id: str | None = None
    try:
        # 创建新 run（写面，持 home 全局锁）。
        with home_lock(create=True):
            state = _upstream_create(topic="", mode="draft", visual_mode="none",
                                     max_images=4, max_image_cost=None, allow_publish=False)
        new_run_id = state.get("run_id")
        if not new_run_id:
            raise internal("创建 run 失败")

        # 预建新 run 锁文件，使后续 run_lock(create=False) 可用（不得放宽为 create=True）。
        ensure_run_lock_file(new_run_id)

        with run_lock(new_run_id, create=False):
            write_run_file(new_run_id, "article.md", content.encode("utf-8"))
            # 记录 parent 关系到 state.yaml
            from . import projection
            new_state, _diags = projection.read_state(new_run_id)
            new_state["parent_run_id"] = run_id
            new_state["fork_source_version"] = version
            new_state["updated"] = projection._now_rfc3339()
            write_state_yaml(new_run_id, yaml.safe_dump(new_state, allow_unicode=True, sort_keys=False))
    except BaseException:
        _rollback_fork(new_run_id, runs_before, current_ptr_before)
        raise

    return {"runId": new_run_id, "parentRunId": run_id, "version": sha256_hex(content.encode("utf-8"))}
