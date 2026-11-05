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
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALL_SCRIPT = REPO_ROOT / "scripts" / "install_deps.py"
WHEEL_DIR = REPO_ROOT / "python-dist"
WHEEL = WHEEL_DIR / "dsh_wewrite_facade-0.1.0-py3-none-any.whl"

# 引导脚本用 `python -m venv`（需 ensurepip），uv 创建的 venv 可能没有 pip；
# 这里取系统 python3（带 ensurepip）跑脚本。测试内寻找，避免硬编码机器绝对路径。
SYSTEM_PYTHON = shutil.which("python3") or shutil.which("python") or sys.executable

# _update_profile_patch 现在用 venv 解释器跑 YAML 处理（装完 facade 后必有 PyYAML）。
# 测试环境本身就跑在装好 PyYAML 的 venv 里，直接用当前 venv 作为目标 venv。
# 注意不能 resolve()（venv 的 bin/python 是 symlink，resolve 会跟到真实解释器）。
VENV_WITH_YAML = Path(sys.executable).parent.parent


def _venv_python(venv: Path) -> Path:
    return venv / "bin" / "python"


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


def test_dry_run_no_system_yaml_required():
    """返修：脚本启动/建 venv/装 wheel 三步零第三方依赖（python3 -S 下也能跑 --dry-run）。"""
    proc = subprocess.run(
        [SYSTEM_PYTHON, "-S", str(INSTALL_SCRIPT), "--dry-run", "--wheel", str(WHEEL)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert "未落盘" in proc.stdout
    # 证明系统 Python 在该隔离下确实没有 yaml（否则本用例失去意义）。
    no_yaml = subprocess.run([SYSTEM_PYTHON, "-S", "-c", "import yaml"], capture_output=True, text=True)
    assert no_yaml.returncode != 0


def test_script_top_level_has_no_yaml_import():
    """返修：脚本源码顶层不得 import yaml（PyYAML 是待安装依赖，不是安装前置）。"""
    raw = INSTALL_SCRIPT.read_text(encoding="utf-8")
    import re
    # 排除子进程内联脚本里的 `import yaml`（那是在 venv 解释器里执行的）。
    body = raw.split("_YAML_PATCH_SCRIPT = r", 1)[0]
    assert re.search(r"^\s*(import\s+yaml|from\s+yaml\s+import)", body, flags=re.MULTILINE) is None


def test_write_profile_config_preserves_existing_custom_fields(tmp_path):
    """返修 3：--write-profile-config 只更新 interpreter/wewriteHome，其余字段原样保留。"""
    mod = _load_module()
    path = tmp_path / "profiles" / "wewrite" / "cordis.patch.yml"
    path.parent.mkdir(parents=True)
    path.write_text(
        "- id: wewrite-python\n"
        "  name: dsh-plugin-wewrite\n"
        "  config:\n"
        "    agentToolsEnabled: true\n"
        "    pushEnabled: true\n"
        "    publishTransport: wechat\n"
        "    bridgeTimeoutMs: 45000\n"
        "    interpreter: /old/python\n"
        "    wewriteHome: /old/home\n",
        encoding="utf-8",
    )
    venv = VENV_WITH_YAML
    home = tmp_path / "data"
    mod._update_profile_patch(path, venv, home, dry_run=False)
    text = path.read_text(encoding="utf-8")
    parsed = yaml.safe_load(text)
    assert isinstance(parsed, list) and len(parsed) == 1
    entry = parsed[0]
    assert entry["id"] == "wewrite-python"
    # 用户自定义字段保留。
    assert entry["config"]["pushEnabled"] is True
    assert entry["config"]["publishTransport"] == "wechat"
    assert entry["config"]["bridgeTimeoutMs"] == 45000
    # 目标字段被更新。
    assert entry["config"]["interpreter"] == str(venv / "bin" / "python")
    assert entry["config"]["wewriteHome"] == str(home)
    # 旧值被移除。
    assert "/old/python" not in text
    assert "/old/home" not in text


def test_write_profile_config_creates_default_when_absent(tmp_path):
    """返修 3：无既有覆盖块时仍追加默认整项。"""
    mod = _load_module()
    path = tmp_path / "profiles" / "wewrite" / "cordis.patch.yml"
    path.parent.mkdir(parents=True)
    venv = VENV_WITH_YAML
    home = tmp_path / "data"
    mod._update_profile_patch(path, venv, home, dry_run=False)
    parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(parsed, list) and len(parsed) == 1
    entry = parsed[0]
    assert entry["id"] == "wewrite-python"
    assert entry["config"]["interpreter"] == str(venv / "bin" / "python")
    assert entry["config"]["wewriteHome"] == str(home)


@pytest.mark.parametrize("input_yaml", [
    # 仅 id 一行。
    "- id: wewrite-python\n",
    # id + name。
    "- id: wewrite-python\n  name: dsh-plugin-wewrite\n",
    # id + name + 其他键（无 config）。
    "- id: wewrite-python\n  name: dsh-plugin-wewrite\n  priority: 10\n",
])
def test_write_profile_config_block_without_config(tmp_path, input_yaml):
    """返修：既有覆盖块没有 config: 时必须新建空映射再挂子键，且输出是合法 YAML。"""
    mod = _load_module()
    path = tmp_path / "profiles" / "wewrite" / "cordis.patch.yml"
    path.parent.mkdir(parents=True)
    path.write_text(input_yaml, encoding="utf-8")
    venv = VENV_WITH_YAML
    home = tmp_path / "data"
    mod._update_profile_patch(path, venv, home, dry_run=False)
    parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(parsed, list) and len(parsed) == 1
    entry = parsed[0]
    assert entry["id"] == "wewrite-python"
    assert isinstance(entry["config"], dict)
    assert entry["config"]["interpreter"] == str(venv / "bin" / "python")
    assert entry["config"]["wewriteHome"] == str(home)


def test_write_profile_config_empty_config(tmp_path):
    """返修：既有块 config: 为空时也要能写入目标字段。"""
    mod = _load_module()
    path = tmp_path / "profiles" / "wewrite" / "cordis.patch.yml"
    path.parent.mkdir(parents=True)
    path.write_text("- id: wewrite-python\n  name: dsh-plugin-wewrite\n  config:\n", encoding="utf-8")
    venv = VENV_WITH_YAML
    home = tmp_path / "data"
    mod._update_profile_patch(path, venv, home, dry_run=False)
    parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(parsed, list) and len(parsed) == 1
    entry = parsed[0]
    assert entry["config"]["interpreter"] == str(venv / "bin" / "python")
    assert entry["config"]["wewriteHome"] == str(home)


def test_write_profile_config_shuffled_fields(tmp_path):
    """返修：字段顺序打乱不影响只更新目标字段、其余保留。"""
    mod = _load_module()
    path = tmp_path / "profiles" / "wewrite" / "cordis.patch.yml"
    path.parent.mkdir(parents=True)
    path.write_text(
        "- id: wewrite-python\n"
        "  name: dsh-plugin-wewrite\n"
        "  priority: 3\n"
        "  config:\n"
        "    bridgeTimeoutMs: 45000\n"
        "    pushEnabled: true\n"
        "    publishTransport: wechat\n"
        "    agentToolsEnabled: true\n",
        encoding="utf-8",
    )
    venv = VENV_WITH_YAML
    home = tmp_path / "data"
    mod._update_profile_patch(path, venv, home, dry_run=False)
    parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
    entry = parsed[0]
    assert entry["priority"] == 3
    assert entry["config"]["pushEnabled"] is True
    assert entry["config"]["publishTransport"] == "wechat"
    assert entry["config"]["bridgeTimeoutMs"] == 45000
    assert entry["config"]["interpreter"] == str(venv / "bin" / "python")
    assert entry["config"]["wewriteHome"] == str(home)


def test_write_profile_config_nested_keys_preserved(tmp_path):
    """返修：块内嵌套子键不被破坏。"""
    mod = _load_module()
    path = tmp_path / "profiles" / "wewrite" / "cordis.patch.yml"
    path.parent.mkdir(parents=True)
    path.write_text(
        "- id: wewrite-python\n"
        "  name: dsh-plugin-wewrite\n"
        "  config:\n"
        "    push:\n"
        "      enabled: true\n"
        "    extra:\n"
        "      nested:\n"
        "        value: keep\n",
        encoding="utf-8",
    )
    venv = VENV_WITH_YAML
    home = tmp_path / "data"
    mod._update_profile_patch(path, venv, home, dry_run=False)
    parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
    entry = parsed[0]
    assert entry["config"]["push"]["enabled"] is True
    assert entry["config"]["extra"]["nested"]["value"] == "keep"
    assert entry["config"]["interpreter"] == str(venv / "bin" / "python")
    assert entry["config"]["wewriteHome"] == str(home)


def test_write_profile_config_idempotent(tmp_path):
    """幂等：连跑两次结果字节级一致，且不重复插入键。"""
    mod = _load_module()
    path = tmp_path / "profiles" / "wewrite" / "cordis.patch.yml"
    path.parent.mkdir(parents=True)
    path.write_text("- id: wewrite-python\n  name: dsh-plugin-wewrite\n", encoding="utf-8")
    venv = VENV_WITH_YAML
    home = tmp_path / "data"
    mod._update_profile_patch(path, venv, home, dry_run=False)
    first = path.read_text(encoding="utf-8")
    mod._update_profile_patch(path, venv, home, dry_run=False)
    second = path.read_text(encoding="utf-8")
    assert first == second
    assert second.count("- id: wewrite-python") == 1
