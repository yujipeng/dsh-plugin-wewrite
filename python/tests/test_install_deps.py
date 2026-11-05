"""JEE-1996 方案 A：依赖安装引导脚本（scripts/install_deps.py）的可判定测试。

- --dry-run 不产生任何落盘副作用；
- 已存在 venv 时幂等复用（不重建）；
- 脚本源码不含机器绝对路径、不写 shell rc / 全局 pip；
- diagnose 自检失败时给出可读原因；
- 随包 wheel 存在且名称符合约定。

全量安装（建 venv → 装 wheel → diagnose 返回 ok:true 且 wewriteVersion=4.2.1）需要真实 PyPI
网络，属交付证据（见 docs/installation.md 与交付评论），不放进入常规测试，避免测试依赖网络。
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALL_SCRIPT = REPO_ROOT / "scripts" / "install_deps.py"
WHEEL_DIR = REPO_ROOT / "python-dist"
WHEEL = WHEEL_DIR / "dsh_wewrite_facade-0.1.0-py3-none-any.whl"

# 引导脚本用 `python -m venv`（需 ensurepip），uv 创建的 venv 可能没有 pip；
# 这里取系统 python3（带 ensurepip）跑脚本。测试内寻找，避免硬编码机器绝对路径。
SYSTEM_PYTHON = shutil.which("python3") or shutil.which("python") or sys.executable


def _load_module():
    spec = importlib.util.spec_from_file_location("install_deps", INSTALL_SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run_script(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [SYSTEM_PYTHON, str(INSTALL_SCRIPT), *args],
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_dry_run_no_side_effects(tmp_path):
    venv = tmp_path / "venv"
    home = tmp_path / "home"
    proc = _run_script("--dry-run", "--venv", str(venv), "--home", str(home), "--wheel", str(WHEEL))
    assert proc.returncode == 0, proc.stderr
    assert not venv.exists(), "--dry-run 不应创建 venv"
    assert not home.exists(), "--dry-run 不应创建数据根"
    assert "未落盘" in proc.stdout


def test_existing_venv_reused(tmp_path):
    """幂等：venv 已存在时脚本必须复用，不重建。"""
    venv = tmp_path / "venv"
    subprocess.run([SYSTEM_PYTHON, "-m", "venv", str(venv)], check=True, capture_output=True)
    marker = venv / "marker.txt"
    marker.write_text("keep", encoding="utf-8")

    proc = _run_script("--dry-run", "--venv", str(venv), "--home", str(tmp_path / "home"), "--wheel", str(WHEEL))
    assert proc.returncode == 0, proc.stderr
    assert "复用已有 venv" in proc.stdout
    assert marker.read_text(encoding="utf-8") == "keep", "复用 venv 时不得重建/清空已有内容"


def test_script_source_has_no_absolute_path_and_no_shell_rc():
    raw = INSTALL_SCRIPT.read_text(encoding="utf-8")
    # shebang 是标准解释器指令（#!/usr/bin/env python3），不属于「机器绝对路径」。
    body = "\n".join(raw.splitlines()[1:])
    for forbidden in ("/home/", "/usr/", "/tmp/", "/var/", "/etc/", "/root/"):
        assert forbidden not in body, f"脚本含机器绝对路径片段 {forbidden}"
    for forbidden in (".bashrc", ".zshrc", "pip config", "pip install --user"):
        assert forbidden not in body, f"脚本不应触碰 {forbidden}"
    # 不写用户 shell 配置：脚本只写 profile 的 cordis.patch.yml，绝不 open shell rc。
    assert "bashrc" not in body and "zshrc" not in body


def test_bundled_wheel_exists():
    assert WHEEL.exists(), f"随包 wheel 缺失：{WHEEL}"
    assert WHEEL.name.startswith("dsh_wewrite_facade-")
    assert WHEEL.name.endswith(".whl")


def test_check_diagnose_rejects_wrong_version():
    mod = _load_module()
    with pytest.raises(SystemExit) as exc:
        mod._check_diagnose({"ok": True, "data": {"wewriteVersion": "9.9.9", "themes": []}})
    assert "4.2.1" in str(exc.value)


def test_check_diagnose_accepts_ok():
    mod = _load_module()
    out = mod._check_diagnose({"ok": True, "data": {"wewriteVersion": "4.2.1", "themes": [{"id": "x"}]}})
    assert "ok=true" in out
    assert "4.2.1" in out


def test_find_wheel_missing_errors(tmp_path):
    mod = _load_module()
    with pytest.raises(SystemExit) as exc:
        mod._find_wheel(tmp_path)
    assert "未找到随包的 facade wheel" in str(exc.value)
