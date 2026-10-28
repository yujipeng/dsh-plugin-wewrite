# 安装与装载

本插件以原生 DSH bundle 分发（TypeScript host + Web client）。目标 DSH 版本为 `0.2.1-rc.4`。

## 前置

- Node.js >= 22.19.0（本机实测 v24.14.1）
- pnpm（本机实测 12.3.4）
- DSH 已安装：`dsh --version` 输出 `0.2.1-rc.4`

## 关键坑：`~/.dsh` 只读

若 `~/.dsh`（默认 `DSH_HOME`）为只读，所有 profile 操作都会失败。必须先指定一个**可写目录**作为 `DSH_HOME`：

```bash
export DSH_HOME=/path/to/writable/dsh-home
```

后续所有 `dsh` 命令都在这个环境变量下执行。`--from-default-profile` 会**重建** profile，只能用于初始化，不能作为每次启动命令。

## 安装步骤

1. **初始化可写 profile（web 模板）**

   ```bash
   DSH_HOME=$DSH_HOME dsh --from-default-profile web --profile wewrite --dump-config
   ```

2. **打包插件（在插件仓库根目录）**

   ```bash
   pnpm install && pnpm build && pnpm pack
   ```

   得到 tarball，例如 `dsh-plugin-wewrite-0.1.0.tgz`。

3. **安装 tarball 到 profile**

   ```bash
   DSH_HOME=$DSH_HOME dsh plugin --profile wewrite add /absolute/path/to/dsh-plugin-wewrite-0.1.0.tgz
   ```

4. **核对插件只出现一次（重复插入即失败）**

   ```bash
   DSH_HOME=$DSH_HOME dsh --profile wewrite --dump-config
   ```

   在输出中搜索 `dsh-plugin-wewrite`，必须只出现一次。

5. **启动 Web**

   ```bash
   DSH_HOME=$DSH_HOME dsh --profile wewrite
   ```

   打开 Web 页面，应能在会话内看到「WeWrite 写作台」tab、侧栏 footer 入口，以及全屏浮层占位面板。

## 卸载

```bash
DSH_HOME=$DSH_HOME dsh plugin --profile wewrite remove dsh-plugin-wewrite
```

## 常见问题

- **`dsh plugin add` 失败**：确认 tarball 是 `pnpm pack` 产物且路径正确；确认 `DSH_HOME` 可写。
- **`dump-config` 里插件出现两次**：说明同时走了 bundle patch 插入和 `--patch ./cordis.patch.yml` 开发态覆盖。开发态才用 `--patch`，不能与 bundle 加载同时重复插入。
- **页面看不到入口位**：确认 Web 已启动、插件在 `dump-config` 中启用，并查看宿主日志中的能力探测诊断。
