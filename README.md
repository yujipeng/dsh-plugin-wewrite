# dsh-plugin-wewrite

DSH Web 工作台 WeWrite 插件。工程骨架与契约层（S1）已在 `main`；本仓库当前阶段（S2）新增
Python facade `python/`（独立包名 `dsh-wewrite-facade`）。

## Python facade（S2）

- 位置：`python/src/dsh_wewrite_facade/`，测试 `python/tests/`。
- 依赖锁定：`python/pyproject.toml`（`wewrite==4.2.1`，运行依赖进 `dependencies`）+
  `python/requirements.lock`（带来源哈希）。
- 安装与自测：

```bash
python3 -m venv .venv
.venv/bin/pip install -e python 'pytest>=8'
cd python && ../.venv/bin/python -m pytest -q
```

- 协议入口：`python -m dsh_wewrite_facade`（单请求单响应，stdout 只允许 JSON）。
- 本阶段 `draft.send` 只接假 transport，真实微信调用计数恒为零；真实推送属 S4。
- 更详细的模块说明见 `python/README.md`。
