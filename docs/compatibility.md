# 兼容性

## 锁定版本

| 组件 | 版本 | 说明 |
|---|---|---|
| DSH | `0.2.1-rc.4` | 宿主运行时，按本机实测面编译 |
| Node.js | `>= 22.19.0`（实测 v24.14.1） | `engines.node` 声明 |
| pnpm | 12.3.4 | 依赖安装，`pnpm-lock.yaml` 锁定 |
| @deepseek-ai/cordis | `4.0.5` | 宿主插件框架（devDependency，external 化） |
| zod | `3.25.76` | 契约 schema（devDependency） |
| react / react-dom | `^18.2.0` | 宿主 peer，外置不打包第二份 |

## 编译原则

- 依赖按本机 `0.2.1-rc.4` 编译，**不照抄参考仓库 `jerryjiao/dsh-wewrite` 的 rc.7 依赖**。
- React 作为宿主 peer 外置：`client.js` 把 `react`、`react-dom`、`@deepseek-ai/*` 全部 external，
  由宿主 ModuleLoader 提供，不打包第二份 Cordis/React。
- 运行依赖不随包分发：本插件是 bundle 骨架，运行期解析走 profile 安装侧的宿主包。

## RPC 信封

- 通道：`/wewrite-python`。
- 传输信封：`{ok:true,value}` / `{ok:false,error:{code,message,details}}`，`code` 收敛为宿主白名单内的 `internal`，
  真实业务错误码只出现在成功传输 value 内的业务失败联合，不写进传输 `error.code`。
- 业务错误码固定（设计 §4）：`INVALID_INPUT`、`NOT_FOUND`、`FORBIDDEN`、`VERSION_CONFLICT`、
  `RUN_BUSY`、`INVALID_TRANSITION`、`CAPABILITY_UNAVAILABLE`、`DEPENDENCY_MISMATCH`、
  `ARTIFACT_MISSING`、`REVIEW_REQUIRED`、`APPROVAL_REQUIRED`、`APPROVAL_DENIED`、
  `APPROVAL_UNAVAILABLE`、`TIMEOUT`、`CANCELLED`、`PUBLISH_UNKNOWN`、`INTERNAL`。

## 能力探测

- 能力探测逐项检查 `connection.rpc` / `skills` / `agents` / `approval` / `effect`。
- 缺失项进入诊断快照（`snapshot` 的 `diagnostics`），并写宿主日志，**不是静默失败**。
- 首版能力面：`read` 可用（snapshot 最小诊断版）；`write`/`preview`/`push` 明确标不可用，属后续 stage。

## 已知边界（S1）

- 不实现业务 RPC（除 `snapshot` 最小诊断版）、不实现 Python facade、不做 UI 面板、不做推送。
- 不修改 `yujipeng/wewrite` 与 `jerryjiao/dsh-wewrite`。
