# 安装与装载

本插件以原生 DSH bundle 分发（TypeScript host + Web client）。目标 DSH 版本为 `0.2.1-rc.4`。

插件通过 `spawn(interpreter, ['-m','dsh_wewrite_facade'])` 调用**外部 Python 进程**，因此除了装 tarball，还必须装齐 Python 依赖。默认走「一条命令」装齐；只在特殊场景才手动分步。

## 为什么不能靠 `postinstall` 自动装依赖

**不要**给插件的 `package.json` 加 `postinstall` / `preinstall` / `install` 脚本，原因已实测（`dsh 0.2.1-rc.5` + pnpm 12.3.4）：

- `dsh plugin add` 把参数**原样转发**给 profile 目录里的 pnpm（`dsh plugin --help`：『forwarding the remaining arguments to pnpm in the profile directory』）。
- 带 `postinstall` 的探针包执行 `dsh plugin add` 会直接报 `ERR_PNPM_IGNORED_BUILDS` 并 exit 1——**不是「钩子没生效」，而是整个安装失败**，钩子根本没执行。
- 反证：当前插件（无任何安装脚本）`dsh plugin add` 是 exit 0。

因此依赖安装必须走**显式、可审阅**的引导脚本，不能藏在装包钩子里。

## 前置

- Node.js >= 22.19.0（本机实测 v24.14.1）
- pnpm（本机实测 12.3.4）
- DSH 已安装：`dsh --version` 输出 `0.2.1-rc.4`
- Python >= 3.11（引导脚本会校验）

## 关键坑：`~/.dsh` 只读

若 `~/.dsh`（默认 `DSH_HOME`）为只读，所有 profile 操作都会失败。必须先指定一个**可写目录**作为 `DSH_HOME`：

```bash
export DSH_HOME=/path/to/writable/dsh-home
```

后续所有 `dsh` 命令都在这个环境变量下执行。`--from-default-profile` 会**重建** profile，只能用于初始化，不能作为每次启动命令。

## 安装步骤（一条命令优先）

1. **打包插件（在插件仓库根目录）**

   ```bash
   pnpm install
   # 重新生成随包 wheel（源码有改动时必做，保证 wheel 与源码一致）
   python3 -m pip wheel --no-deps --no-build-isolation -w python-dist python
   pnpm build && pnpm pack
   ```

   得到 tarball（例如 `dsh-plugin-wewrite-0.1.0.tgz`）。tarball 内已包含 Python wheel（`python-dist/*.whl`）与引导脚本（`scripts/install_deps.py`）。

2. **安装 tarball 到 profile**

   ```bash
   DSH_HOME=$DSH_HOME dsh plugin --profile wewrite add /absolute/path/to/dsh-plugin-wewrite-0.1.0.tgz
   ```

3. **一条命令装齐 Python 依赖（建 venv + 装 wheel + 自检）**

   ```bash
   python3 <插件安装目录>/scripts/install_deps.py
   ```

   脚本会：探测 Python ≥3.11 → 在 `~/.local/share/dsh-wewrite/venv` 建（或复用）venv → 从**包内自带 wheel** 安装 `dsh_wewrite_facade`（其声明的 `wewrite==4.2.1` 等依赖由 pip 从 PyPI 解析）→ 打印可直接复制的两行 `export WEWRITE_PYTHON=... / WEWRITE_HOME=...` → 最后跑一次 `diagnose` 自检，输出 `ok=true、wewriteVersion=4.2.1`。

   `<插件安装目录>` 在 profile 的 `node_modules/dsh-plugin-wewrite` 下（用 `find $DSH_HOME -name install_deps.py` 定位）。也可以用 `--write-profile-config` 直接把解释器与数据根写进 profile 的 `cordis.patch.yml`：

   ```bash
   python3 <插件安装目录>/scripts/install_deps.py \
     --write-profile-config --profile wewrite --dsh-home "$DSH_HOME"
   ```

   常用参数：`--venv <path>`（覆盖 venv 路径）、`--home <path>`（覆盖 `WEWRITE_HOME` 数据根）、`--dry-run`（只打印不落盘）、`--no-diagnose`（跳过自检）。脚本**幂等**：重复执行复用已有 venv、不重建、不破坏已有配置。

4. **启动 Web（带上脚本打印的两行环境变量）**

   ```bash
   export DSH_HOME=$DSH_HOME
   export WEWRITE_PYTHON=<脚本打印的 venv python 路径>
   export WEWRITE_HOME=<脚本打印的数据根路径>
   dsh --profile wewrite
   ```

   打开 Web 页面，应能在会话内看到「WeWrite 写作台」tab、侧栏 footer 入口，以及全屏浮层占位面板。若工作台报 facade 不可用，先检查 `WEWRITE_PYTHON` / `WEWRITE_HOME` 是否指向脚本给出的路径。

## 手动分步安装（特殊场景）

1. **初始化可写 profile（web 模板）**

   ```bash
   DSH_HOME=$DSH_HOME dsh --from-default-profile web --profile wewrite --dump-config
   ```

2. **建 venv 并装 facade wheel**

   ```bash
   python3 -m venv ~/wewrite-venv
   ~/wewrite-venv/bin/pip install /absolute/path/to/dsh_wewrite_facade-0.1.0-py3-none-any.whl
   ```

   自检：

   ```bash
   echo '{"protocol":1,"requestId":"r","operation":"diagnose","payload":{}}' \
     | WEWRITE_HOME=~/wewrite-data ~/wewrite-venv/bin/python -m dsh_wewrite_facade
   ```

   预期输出含 `"ok": true` 且 `wewriteVersion=4.2.1`。

3. **设环境变量并启动**

   ```bash
   export WEWRITE_PYTHON=~/wewrite-venv/bin/python
   export WEWRITE_HOME=~/wewrite-data
   DSH_HOME=$DSH_HOME dsh --profile wewrite
   ```

## 装不全时，产品内会主动引导

设置面板会显示「缺什么 → 一条可复制的安装命令 → 装完点『重新检测』」。「重新检测」会真正重新探测解释器与数据根，而不是重渲染旧快照。插件自身**不代用户执行** `pip install`，只呈现命令，守住「安装不隐式执行未经审阅的脚本」这条设计约束。

## 卸载

```bash
DSH_HOME=$DSH_HOME dsh plugin --profile wewrite remove dsh-plugin-wewrite
```

## 常见问题

- **`dsh plugin add` 失败**：确认 tarball 是 `pnpm pack` 产物且路径正确；确认 `DSH_HOME` 可写；确认插件 `package.json` **没有** `postinstall`/`preinstall`/`install` 脚本（有则会被 pnpm 12 直接拒绝）。
- **`install_deps.py` 报「未找到随包的 facade wheel」**：在插件仓库根目录执行 `python3 -m pip wheel --no-deps --no-build-isolation -w python-dist python` 重新生成 wheel，再 `pnpm pack`。
- **facade 自检失败**：看脚本打印的可读原因（不是异常栈）；常见是 venv 里 `wewrite` 版本不对，或 `WEWRITE_HOME` 指向不可写目录。
- **`dump-config` 里插件出现两次**：说明同时走了 bundle patch 插入和 `--patch ./cordis.patch.yml` 开发态覆盖。开发态才用 `--patch`，不能与 bundle 加载同时重复插入。
- **页面看不到入口位**：确认 Web 已启动、插件在 `dump-config` 中启用，并查看宿主日志中的能力探测诊断。
