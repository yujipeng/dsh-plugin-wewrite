# dsh-plugin-wewrite

DSH Web 工作台 WeWrite 插件（`dsh-plugin-wewrite`）。

- S1：工程骨架与契约层（`src/shared/contract.ts` 全 RPC schema + 双端契约测试）。
- S2：Python facade（`python/src/dsh_wewrite_facade/`，独立包名 `dsh-wewrite-facade`）。
- S3（本阶段）：业务 RPC / Agent 工具 / skills 随包装载 / 工作台 UI。

## 模块

| 模块 | 说明 |
|---|---|
| `src/shared/contract.ts` | 契约真源：17 个 RPC + 8 个 Agent 工具的严格 schema（拒绝未知字段）。 |
| `src/host/workbench-service.ts` | 统一业务 Module：RPC 与工具是同一 Interface 的两个 Adapter。 |
| `src/host/agent-workflow.ts` | 创建/恢复/取消 Agent 与 run 绑定，落实接管栅栏（CAS + generation 原子递增 + 新 workflowId）。 |
| `src/host/wewrite-bridge.ts` | 固定解释器 JSON 子进程协议 + 子进程回收。 |
| `src/host/tools/index.ts` | 8 个受控 Agent 工具（`wewrite_push_draft` 属 S4 不注册）。 |
| `src/host/skills-provider.ts` | 随包 skills Provider（`wewrite-python-bundle`，固定 manifest 加载，禁止目录扫描）。 |
| `src/client/panels/` | 任务/选题/编辑器/审稿/设置面板，四态不白屏。 |
| `skills/` | 6 个适配 skill（主 skill 接收已创建 run，不执行 run start/resume）。 |
| `python/` | facade：读面投影 / 写锁 / 路径安全 / 版本 CAS / binding 栅栏。 |

## 契约硬约束（已定死）

- 业务失败放成功值内的 `{ok:false,error}`；传输层失败才用宿主 `ConnectionRpcResult` 的 `ok:false`。
- `agents.create()` 返回 `AgentHandle`，必须 `handle.agent` 取 Agent，持有 `handle.dispose()`。
- `Agent.cancel()` 首参是 `AgentCancelCause` 判别联合，不传裸字符串。
- 接管栅栏：CAS + 旧绑定失效 + generation 原子递增 + 撤销旧 intent（同一可恢复事务），再发布新 Binding。
- 每次工具执行从真实 `exec.agent` 查绑定；generation 取服务端绑定，不信模型自报。
- `restrict()` 只过滤继承层；自身层与 reserved `run_code` 由 scope 内单调 guard 拒绝。

## 安装与自测

```bash
pnpm install
python3 -m venv .venv
.venv/bin/pip install -e python 'pytest>=8'

pnpm typecheck
pnpm test                 # vitest：host/client/契约/工具/skills/bridge e2e
pnpm build                # lib/index.js、lib/shared.js、lib/client.js
PYTHONPATH=python/src .venv/bin/python -m pytest -q   # facade 测试
```

## 微信推送（S4）

本阶段 `wechat/prepareDraft` 只做本地预检，`wechat/pushDraft` 转入宿主审批的操作位
（`pending_approval`），不真发；`draft.send` 只接假 transport，真实微信调用计数恒为零。
