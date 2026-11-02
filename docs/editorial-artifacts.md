# 产物填写规则（editorial_schema: 2）

本文档是受控写作 Agent 填写 `sources` / `claims` / `brief` 的产物内容规则，随
`followupText()` 与审稿入口下发。**不是**传输契约；`src/shared/contract.ts` 与工具参数不变。

## 核心口径

审稿 `pass` 只证明「引用链与声明结构完整」，**不验证语义与来源真实性**。事实核对与结论有用性由模型五维审稿和人工逐篇推送确认承担。

## 填写顺序（必须）

1. 完成正文并保存最终 `article`；回读 `article` 拿真实 `version`。正文定稿后不得再改。
2. 写 `sources`：每条 `evidence.text` 是**完整原文直引**，`evidence.sha256` 可省略由服务端补齐，`locator` 必填。
3. 写 `brief` 的 `goal.takeaway` / `goal.action`（至少一个非空）与 `delivery.anchors`（正文精确锚点）。
4. 写 `claims` 的 `article_hash`、每条 `fact` 的 `article_span` 与 `evidence_refs`，以及 `blocks` 覆盖清单。
5. 调用 `wewrite_review`。

## 字段示例

见 `redline-decision-design-v2.md` §2 与 §10。`quote` 必须等于整段 `evidence.text`，不是任选子串；`claim.text` 必须等于 `quote`。

## 语义边界

- `verified`、URL、原文段落由 Agent 填写；哈希只证本地一致，不证来自网页。
- 空话被填进 `takeaway` 且正文严格重复时，结构校验会放行——这是已接受的能力边界，语义审核责任仍在模型与人工。
