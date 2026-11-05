#!/usr/bin/env python3
"""dsh-plugin-wewrite 依赖安装引导脚本（JEE-1996 方案 A）。

把「建 venv + 装 facade wheel + 设两个环境变量」压成一条命令。脚本本身**只做显式、
可审阅、可见**的事，绝不隐式执行：

- 不修改全局环境、不写用户 shell rc、不带任何机器绝对路径（路径一律运行时计算）。
- 不联网拉取本仓库代码：只安装包内自带的 ``dsh_wewrite_facade`` wheel，
  其声明的依赖（``wewrite==4.2.1`` / ``PyYAML`` 等）由 pip 从 PyPI 解析。
- 幂等：venv 已存在则复用，重复执行不重建、不破坏已有配置。
- ``--dry-run`` 只打印将要执行的动作，不产生任何落盘副作用。

重要（写给后人，勿再试）：**绝不能**给插件的 package.json 加 ``postinstall`` /
``preinstall`` / ``install`` 脚本——``dsh plugin add`` 会把参数原样转发给 profile 目录里的
pnpm，pnpm 12 对带安装钩子的包直接报 ``ERR_PNPM_IGNORED_BUILDS`` 导致**整个安装失败**，
钩子根本不会执行（实测于 dsh 0.2.1-rc.5 + pnpm 12.3.4）。因此本脚本只能由用户显式执行，
或由设置面板**只呈现命令、不代执行**。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

FACADE_MODULE = "dsh_wewrite_facade"
WHEEL_PREFIX = "dsh_wewrite_facade-"

# 与 src/host/index.ts 的 Config 对齐：profile 私有覆盖配置通过这两个键注入。
PLUGIN_ID = "wewrite-python"
PLUGIN_NAME = "dsh-plugin-wewrite"


def _default_venv() -> Path:
    """默认 venv 路径：~/.local/share/dsh-wewrite/venv（运行时计算）。"""
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "dsh-wewrite" / "venv"


def _default_home() -> Path:
    """默认数据根：~/.local/share/dsh-wewrite/data（运行时计算）。"""
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "dsh-wewrite" / "data"


def _script_root() -> Path:
    """包根目录：本脚本位于 <包根>/scripts/install_deps.py。"""
    return Path(__file__).resolve().parent.parent


def _bundled_wheel_dir() -> Path:
    """随包分发的 wheel 目录（package.json ``files`` 中的 python-dist）。"""
    return _script_root() / "python-dist"


def _find_wheel(wheel_dir: Path) -> Path:
    """定位包内唯一一个 facade wheel；缺失/多于一个都报中文错误。"""
    candidates = sorted(wheel_dir.glob(WHEEL_PREFIX + "*.whl"))
    if not candidates:
        raise SystemExit(
            f"未找到随包的 facade wheel：{wheel_dir} 下没有 {WHEEL_PREFIX}*.whl。"
            "请在插件仓库根目录重新生成 wheel：python3 -m pip wheel --no-deps --no-build-isolation -w python-dist python"
            "，然后重新 pnpm pack。"
        )
    if len(candidates) > 1:
        names = "、".join(c.name for c in candidates)
        raise SystemExit(f"python-dist 下存在多个 facade wheel（{names}），无法确定要安装哪一个。")
    return candidates[0]


def _venv_python(venv: Path) -> Path:
    if os.name == "nt":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def _check_python() -> None:
    """探测当前解释器（即用户用来跑本脚本的 python3）版本 ≥ 3.11。"""
    if sys.version_info < (3, 11):
        raise SystemExit(
            f"需要 Python ≥ 3.11，当前是 {sys.version_info.major}.{sys.version_info.minor}。"
            "请用 python3（≥ 3.11）执行本脚本。"
        )


def _export_lines(venv: Path, home: Path) -> list[str]:
    python = _venv_python(venv)
    return [
        f"export WEWRITE_PYTHON={python}",
        f"export WEWRITE_HOME={home}",
    ]


def _ensure_venv(venv: Path, *, dry_run: bool) -> tuple[bool, str]:
    """建/复用 venv；返回 (是否新建, 中文说明)。幂等：已存在则不重建。"""
    python = _venv_python(venv)
    if python.exists() and os.access(python, os.X_OK):
        return False, f"复用已有 venv：{venv}"
    if dry_run:
        return True, f"[dry-run] 将创建 venv：{venv}"
    venv.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [sys.executable, "-m", "venv", str(venv)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return True, f"已创建 venv：{venv}"


def _install_wheel(venv: Path, wheel: Path, *, dry_run: bool) -> str:
    python = _venv_python(venv)
    cmd = [str(python), "-m", "pip", "install", "--disable-pip-version-check", str(wheel)]
    if dry_run:
        return f"[dry-run] 将执行：{' '.join(cmd)}"
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if proc.returncode != 0:
        tail = (proc.stdout or "").strip().splitlines()[-12:]
        raise SystemExit(
            "facade wheel 安装失败，pip 返回非零。下面是 pip 输出的最后几行（可据此排查 PyPI 依赖解析问题）：\n"
            + "\n".join(tail)
        )
    return f"已从包内 wheel 安装 {wheel.name}（依赖由 pip 从 PyPI 解析）"


def _run_diagnose(venv: Path, home: Path) -> dict:
    """用 venv 里的解释器发一条 diagnose 自检，返回 parsed JSON（失败抛中文错误）。"""
    python = _venv_python(venv)
    env = os.environ.copy()
    env["WEWRITE_HOME"] = str(home)
    req = json.dumps({"protocol": 1, "requestId": "install-self-check", "operation": "diagnose", "payload": {}}, ensure_ascii=False)
    try:
        proc = subprocess.run(
            [str(python), "-m", FACADE_MODULE],
            input=req + "\n",
            capture_output=True,
            text=True,
            env=env,
            timeout=60,
        )
    except subprocess.TimeoutExpired as exc:
        raise SystemExit("diagnose 自检超时：facade 子进程未在 60 秒内返回。") from exc
    if proc.returncode != 0:
        raise SystemExit(f"diagnose 自检失败：facade 子进程退出码 {proc.returncode}（stderr：{(proc.stderr or '').strip()}）")
    out = proc.stdout.strip()
    if not out:
        raise SystemExit("diagnose 自检失败：facade 子进程无输出。")
    try:
        resp = json.loads(out)
    except json.JSONDecodeError as exc:
        raise SystemExit("diagnose 自检失败：facade 输出不是合法 JSON。") from exc
    return resp


def _check_diagnose(resp: dict) -> str:
    """校验 diagnose 结果；不满足给可读原因（不是异常栈）。"""
    if resp.get("ok") is not True:
        err = resp.get("error") or {}
        raise SystemExit(f"diagnose 自检失败：{err.get('code', 'UNKNOWN')} {err.get('message', '')}".strip())
    data = resp.get("data") or {}
    version = data.get("wewriteVersion")
    if version != "4.2.1":
        raise SystemExit(f"diagnose 自检失败：wewriteVersion 应为 4.2.1，实际 {version!r}。")
    themes = data.get("themes") or []
    return (
        "diagnose 自检通过：ok=true，wewriteVersion=4.2.1，"
        f"facade 0.1.0，可用主题 {len(themes)} 个。"
    )


def _profile_patch_path(dsh_home: Path, profile: str) -> Path:
    return dsh_home / "profiles" / profile / "cordis.patch.yml"


def _override_entry(venv: Path, home: Path) -> list[str]:
    """生成写进 profile cordis.patch.yml 的整项覆盖（补丁按整项替换，须给全 config）。"""
    python = _venv_python(venv)
    return [
        "- id: wewrite-python",
        "  name: dsh-plugin-wewrite",
        "  config:",
        "    agentToolsEnabled: true",
        "    pushEnabled: false",
        "    publishTransport: fake",
        "    bridgeTimeoutMs: 30000",
        f"    interpreter: {python}",
        f"    wewriteHome: {home}",
    ]


def _update_profile_patch(path: Path, entry: list[str], *, dry_run: bool) -> str:
    """幂等写入 profile patch：已存在 wewrite-python 覆盖则整体替换，否则追加。"""
    if dry_run:
        return f"[dry-run] 将写入 profile 覆盖配置：{path}"
    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()
    else:
        lines = []
    # 定位并移除既有顶层 `- id: wewrite-python` 覆盖块（不含 `- insert:` 内的同名行）。
    out: list[str] = []
    i = 0
    replaced = False
    while i < len(lines):
        line = lines[i]
        if line == "- id: wewrite-python":
            replaced = True
            # 跳过该覆盖块直到下一个顶层 `- ` 或 EOF。
            i += 1
            while i < len(lines) and not lines[i].startswith("- "):
                i += 1
            continue
        out.append(line)
        i += 1
    if replaced:
        out.extend(entry)
    else:
        # 保持一个空行分隔后追加；文件为空则直接作为列表首项。
        if out and out[-1].strip() != "":
            out.append("")
        out.extend(entry)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")
    os.replace(tmp, path)
    action = "已更新" if replaced else "已追加"
    return f"{action} profile 覆盖配置：{path}"


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="一条命令装齐 dsh-plugin-wewrite 的 Python 依赖（venv + facade wheel + 自检）。",
    )
    parser.add_argument("--venv", type=str, default=str(_default_venv()), help="venv 路径（默认 ~/.local/share/dsh-wewrite/venv）")
    parser.add_argument("--home", type=str, default=str(_default_home()), help="WEWRITE_HOME 数据根（默认 ~/.local/share/dsh-wewrite/data）")
    parser.add_argument("--wheel", type=str, default=None, help="显式指定 facade wheel 路径（默认取包内 python-dist 下的 wheel）")
    parser.add_argument("--dry-run", action="store_true", help="只打印将要执行的动作，不落盘")
    parser.add_argument("--no-diagnose", action="store_true", help="跳过结尾的 diagnose 自检")
    parser.add_argument("--write-profile-config", action="store_true", help="把解释器/数据根写进 profile 的 cordis.patch.yml（整项覆盖）")
    parser.add_argument("--profile", type=str, default=None, help="--write-profile-config 时的 profile 名")
    parser.add_argument("--dsh-home", type=str, default=None, help="DSH_HOME 目录（默认取 $DSH_HOME 或 ~/.dsh）")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(list(sys.argv[1:] if argv is None else argv))
    _check_python()

    venv = Path(args.venv).expanduser()
    home = Path(args.home).expanduser()
    wheel_dir = _bundled_wheel_dir()
    wheel = Path(args.wheel).expanduser() if args.wheel else _find_wheel(wheel_dir)
    dry = args.dry_run

    steps: list[str] = []

    created, venv_msg = _ensure_venv(venv, dry_run=dry)
    steps.append(venv_msg)
    steps.append(_install_wheel(venv, wheel, dry_run=dry))

    if args.write_profile_config:
        profile = args.profile
        if not profile:
            raise SystemExit("--write-profile-config 需要同时提供 --profile <name>。")
        dsh_home = Path(args.dsh_home).expanduser() if args.dsh_home else Path(os.environ.get("DSH_HOME", Path.home() / ".dsh"))
        steps.append(_update_profile_patch(_profile_patch_path(dsh_home, profile), _override_entry(venv, home), dry_run=dry))

    exports = _export_lines(venv, home)
    if dry:
        print("将执行（--dry-run，未落盘）：")
        for s in steps:
            print(f"  · {s}")
        print("完成后可复制下面的环境变量（本次未实际安装，路径仅示意）：")
        for line in exports:
            print(f"  {line}")
        return 0

    print("\n".join(f"· {s}" for s in steps))
    print("请把下面两行加入启动 DSH Web 的环境（或已通过 --write-profile-config 写入 profile）：")
    for line in exports:
        print(line)

    if args.no_diagnose:
        print("已跳过 diagnose 自检。")
        return 0

    resp = _run_diagnose(venv, home)
    print(_check_diagnose(resp))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except subprocess.CalledProcessError as exc:
        tail = (exc.output or "").strip().splitlines()[-12:]
        print(f"命令失败（退出码 {exc.returncode}）：{' '.join(exc.cmd)}", file=sys.stderr)
        if tail:
            print("\n".join(tail), file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
