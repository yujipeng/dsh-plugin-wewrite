# dsh_wewrite_facade

插件与 WeWrite 之间的唯一受控 Python 通道（`dsh-plugin-wewrite` 的 Python facade）。
独立包名，禁止在新仓库放残缺 `wewrite` 包遮蔽上游。

## 协议

JSON 子进程协议（设计 §6），单请求单响应：

```json
{"protocol":1,"requestId":"…","operation":"article.save","payload":{…}}
```

响应 `{protocol:1,requestId,ok:true,data}` 或 `{protocol:1,requestId,ok:false,error}`。
stdout 只允许 JSON，stderr 是脱敏诊断。operation 白名单见 `contract.OPERATIONS`。

## 拒绝放行的契约

- 审稿必须绑定当前正文的实际字节 SHA-256。`article_hash` 为首选字段；仅在该字段不存在时兼容 `articleVersion`。缺失或非法绑定返回 `publishable:false`，并带阻断诊断 `REVIEW_BINDING_MISSING`；哈希不符返回 `REVIEW_REQUIRED`。只有绑定匹配且 `publishable` 为布尔值 `true` 才通过。报告存在而正文缺失时返回业务失败 `ARTIFACT_MISSING`，不伪造 `GateSchema.articleVersion`。
- 输出超限终止并回收确切子进程，`RunResult.issue` 返回 `{code:"OUTPUT_TOO_LARGE",message:"子进程输出超限",blocking:true}`。该字段是 runner 本地诊断，使用 `IssueSchema` 的字段和开放诊断码，不加入共享业务错误码枚举；`response` 为空，两个超时标志均为 `false`。调用方应先检查 `issue`，再处理超时或子进程响应；本地诊断不冒充子进程 JSON 响应。
- YAML 损坏、非映射或非 UTF-8 状态保留为失败条目：`title="状态不可读"`、`pythonStatus="unreadable"`、`phase="failed"`、`editable=false`，更新时间取状态文件实际 mtime。共享摘要要求 `mode` 必填，因此以 `draft` 占位，仅用于满足形状，不代表恢复出业务状态。详情返回同一失败条目、阻断诊断 `lastError.code="STATE_CORRUPTED"`、空步骤/产物与空 gate；不读取正常业务摘要。`artifact.get` 返回 `STATE_CORRUPTED`。所有这些读操作不修改文件、目录或锁设施。

第 5 轮契约修订采用方案 A：`STATE_CORRUPTED` 正式加入双端业务错误码，共 18 项；损坏状态下 `artifact.get` 与写入口统一返回该码，不再返回 `INVALID_TRANSITION`。只读详情与列表仍以同名诊断保留不可读条目。沿用 `IssueSchema`、`RunSummarySchema`、`GateSchema` 的形状；共享常量及其业务失败校验枚举同步更新，无需修改 host/client 实现。

## 安装与运行

```bash
python -m venv .venv
.venv/bin/pip install -e python
echo '{"protocol":1,"requestId":"r1","operation":"diagnose","payload":{}}' | \
  WEWRITE_HOME=/tmp/wewrite .venv/bin/python -m dsh_wewrite_facade
```

## 模块

- `protocol.py` — JSON 子进程协议
- `contract.py` — operation 白名单与 payload 校验
- `paths.py` — 路径安全（openat + O_NOFOLLOW）
- `projection.py` — 只读投影（绝不写盘）
- `storage.py` — 写锁、原子写、版本 CAS、journal
- `review.py` / `hotspots.py` — 复用上游 build_report / 热榜 / 排版
- `assets.py` — 资产登记与图片安全
- `publish.py` — 假 transport（本 stage 真实微信调用恒为零）
- `service.py` — operation 分发
