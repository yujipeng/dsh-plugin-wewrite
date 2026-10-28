"""验收 4：并发——同版 100 次 article.save 最多一个成功，其余 VERSION_CONFLICT；
并发写不同 run 互不阻塞。"""

from __future__ import annotations

import concurrent.futures
import json
import subprocess
import sys

from conftest import call_facade, create_run, facade_env, make_home, write_article


def _save(home: str, run_id: str, version: str, idx: int) -> dict:
    req = json.dumps({
        "protocol": 1,
        "requestId": f"s{idx}",
        "operation": "article.save",
        "payload": {
            "requestId": f"s{idx}",
            "runId": run_id,
            "markdown": f"# 内容 {idx}\n",
            "expectedVersion": version,
            "metadata": {"title": f"标题{idx}", "digest": "摘要", "themeId": "professional-clean"},
        },
    }, ensure_ascii=False)
    proc = subprocess.run(
        [sys.executable, "-m", "dsh_wewrite_facade"],
        input=req + "\n", capture_output=True, text=True, env=facade_env(home), timeout=60,
    )
    return json.loads(proc.stdout.strip())


def test_concurrent_save_same_version_one_success(tmp_path):
    home = make_home(tmp_path)
    run_id = create_run(home)
    version = write_article(home, run_id, "初始正文\n")

    n = 100
    with concurrent.futures.ThreadPoolExecutor(max_workers=32) as pool:
        results = list(pool.map(lambda i: _save(str(home), run_id, version, i), range(n)))

    ok = [r for r in results if r.get("ok") is True]
    conflicts = [r for r in results if r.get("ok") is False and r["error"]["code"] == "VERSION_CONFLICT"]

    assert len(ok) <= 1, f"同版并发保存应最多一个成功，实际 {len(ok)} 个"
    assert len(ok) == 1, "应恰好一个成功"
    assert len(conflicts) == n - 1, f"其余应 VERSION_CONFLICT，实际 {len(conflicts)}"


def test_concurrent_save_different_runs_no_block(tmp_path):
    home = make_home(tmp_path)
    run_a = create_run(home, "A")
    run_b = create_run(home, "B")
    va = write_article(home, run_a, "A 正文\n")
    vb = write_article(home, run_b, "B 正文\n")

    def save_ab(i):
        target = run_a if i % 2 == 0 else run_b
        v = va if i % 2 == 0 else vb
        return _save(str(home), target, v, i)

    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(save_ab, range(40)))

    ok = [r for r in results if r.get("ok") is True]
    # 不同 run 用各自 run 锁，互不阻塞；每 run 第一次保存成功，后续同 run 冲突。
    assert len(ok) == 2, f"不同 run 各成功一次，实际 {len(ok)}"
