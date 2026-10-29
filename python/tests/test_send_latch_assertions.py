"""四轮裁定 A 的三条机械断言 + 反向验证（发送持门闩期间摘要写路径的 RUN_BUSY 语义）。

断言在「另一进程已持 send 门闩」的条件下执行：
1. artifact.write(kind=article) → RUN_BUSY 且 article.md sha256/字节不变。
2. artifact.write(kind=sources) → 成功并读回新值。
3. artifact.write(kind=review_report) → RUN_BUSY 且 review-report.json sha256 不变。
反向验证：故意去掉 article 门闩 / 给 sources 错加门闩 / 提前释放门闩，断言必须失败。
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import multiprocessing
import os
import subprocess
import sys
import time

import pytest

from conftest import call_facade, create_run, facade_env, make_home, write_article


def _hold_send_latch(home: str, run_id: str, ready: multiprocessing.Event, release: multiprocessing.Event):
    """子进程持住 run 的 send.lock，直到收到 release。"""
    run_dir = os.path.join(home, "runs", run_id)
    wb_dir = os.path.join(run_dir, ".dsh-workbench")
    os.makedirs(wb_dir, exist_ok=True)
    fd = os.open(os.path.join(wb_dir, "send.lock"), os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    ready.set()
    release.wait(timeout=30)
    fcntl.flock(fd, fcntl.LOCK_UN)
    os.close(fd)


def _sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def _write_artifact(home, run_id, kind, content, expected=None):
    return call_facade(str(home), "artifact.write", {
        "requestId": f"w-{kind}", "runId": run_id, "kind": kind,
        "content": content, "expectedVersion": expected,
    })


@pytest.fixture()
def latched_home(tmp_path):
    home = make_home(tmp_path)
    run_id = create_run(home)
    write_article(home, run_id, "# 初始\n\n正文")
    # 预建 .dsh-workbench/send.lock（写路径会建，这里显式建以免子进程 open 失败）。
    run_dir = home / "runs" / run_id
    (run_dir / ".dsh-workbench").mkdir(parents=True, exist_ok=True)
    (run_dir / ".dsh-workbench" / "send.lock").touch()
    return home, run_id


def _run_with_latch(home, run_id, fn):
    ready = multiprocessing.Event()
    release = multiprocessing.Event()
    p = multiprocessing.Process(target=_hold_send_latch, args=(str(home), run_id, ready, release))
    p.start()
    assert ready.wait(timeout=10), "持锁子进程未就绪"
    try:
        return fn()
    finally:
        release.set()
        p.join(timeout=10)
        if p.is_alive():
            p.terminate()
            p.join()


def test_article_write_run_busy_while_latched(latched_home):
    home, run_id = latched_home
    article_path = home / "runs" / run_id / "article.md"
    before_sha = _sha(str(article_path))
    before_bytes = article_path.stat().st_size

    resp = _run_with_latch(home, run_id, lambda: _write_artifact(home, run_id, "article", "# 新正文\n"))

    assert resp["ok"] is False, resp
    assert resp["error"]["code"] == "RUN_BUSY", resp
    assert _sha(str(article_path)) == before_sha, "article.md 被改写"
    assert article_path.stat().st_size == before_bytes, "article.md 字节数变化"


def test_sources_write_succeeds_while_latched(latched_home):
    home, run_id = latched_home
    # sources 有预建文件，需读当前版本。
    get_resp = call_facade(str(home), "artifact.get", {"runId": run_id, "kind": "sources"})
    current_version = get_resp["data"]["version"]
    new_content = "version: 2\nrun_id: r\nsources:\n  - id: s-new\n    title: 新来源\n"

    resp = _run_with_latch(home, run_id, lambda: _write_artifact(home, run_id, "sources", new_content, current_version))

    assert resp["ok"] is True, resp  # 必须成功，不是「不是 RUN_BUSY」
    get2 = call_facade(str(home), "artifact.get", {"runId": run_id, "kind": "sources"})
    assert get2["data"]["content"] == new_content


def test_review_report_write_run_busy_while_latched(latched_home):
    home, run_id = latched_home
    run_dir = home / "runs" / run_id
    report_path = run_dir / "review-report.json"
    report_content = '{"version": 1, "decision": "pass", "publishable": true}'
    report_path.write_text(report_content, encoding="utf-8")
    before_sha = _sha(str(report_path))

    resp = _run_with_latch(home, run_id, lambda: _write_artifact(home, run_id, "review_report", '{"version": 1, "decision": "revise", "publishable": false}', None))

    assert resp["ok"] is False, resp
    assert resp["error"]["code"] == "RUN_BUSY", resp
    assert _sha(str(report_path)) == before_sha, "review-report.json 被改写"


# ── 反向验证（故意改坏则测试必须失败；在进程内 monkeypatch，不走子进程）────────────────

def test_reverse_removing_article_latch_fails(monkeypatch, latched_home):
    """去掉 article 门闩后，article 直写应成功——证明门闩是 load-bearing，去掉就绕过。"""
    home, run_id = latched_home
    monkeypatch.setenv("WEWRITE_HOME", str(home))
    from dsh_wewrite_facade import service

    # 模拟「实现者漏加 article 门闩」：把 latched kind 清空。
    monkeypatch.setattr(service, "_SEND_LATCHED_KINDS", frozenset())

    def run_in_process():
        from dsh_wewrite_facade import service as svc
        from dsh_wewrite_facade import projection
        from dsh_wewrite_facade.errors import FacadeError
        _, cur_version = projection.read_artifact_content(run_id, "article")
        try:
            return svc.op_artifact_write({
                "requestId": "w", "runId": run_id, "kind": "article",
                "content": "# 绕过\n", "expectedVersion": cur_version,
            })
        except FacadeError as exc:
            return {"ok": False, "error": {"code": exc.code, "message": str(exc), "retryable": exc.retryable}}

    resp = _run_with_latch(home, run_id, run_in_process)
    # 反证：去掉门闩后，即使另一进程持 send 门闩，article 直写也会成功 → 说明门闩被绕过。
    assert resp["ok"] is True, resp


def test_reverse_wrongly_latching_sources_fails(monkeypatch, latched_home):
    """给 sources 错加门闩后，sources 直写应 RUN_BUSY——证明误加门闩会被测出。"""
    home, run_id = latched_home
    monkeypatch.setenv("WEWRITE_HOME", str(home))
    from dsh_wewrite_facade import service

    monkeypatch.setattr(service, "_SEND_LATCHED_KINDS",
                        frozenset({"article", "draft", "review_report", "sources"}))

    def run_in_process():
        from dsh_wewrite_facade import service as svc
        from dsh_wewrite_facade.errors import FacadeError
        try:
            return svc.op_artifact_write({
                "requestId": "w", "runId": run_id, "kind": "sources",
                "content": "version: 2\nsources: []\n", "expectedVersion": None,
            })
        except FacadeError as exc:
            return {"ok": False, "error": {"code": exc.code, "message": str(exc), "retryable": exc.retryable}}

    resp = _run_with_latch(home, run_id, run_in_process)
    # 反证：给 sources 错加门闩后，另一进程持门闩时 sources 直写会被拒 → 说明误加门闩会误伤。
    assert resp["ok"] is False
    assert resp["error"]["code"] == "RUN_BUSY"
