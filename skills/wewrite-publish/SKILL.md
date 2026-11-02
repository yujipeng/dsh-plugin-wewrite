---
description: 预览与请求草稿推送（本地预览 + 预检 + 逐篇确认请求；不真实推送到微信）
whenToUse: 工作台完成审稿后需要预览或发起逐篇确认推送请求时
---

# wewrite-publish — 排版、预览、草稿箱

## 前置

用 `wewrite_run_detail` 读取当前 run。如果 `illustrated_article` 存在且非空就使用它，否则使用
`article`；读取标题、摘要、封面、预览路径、发布权限和降级标记。已完成文章可以直接排版或预览，
不要恢复写作任务。主题取工作台传入的 themeId。

完整读取：

```text
读取: {skill_dir}/references/wechat-constraints.md
```

排版和预览不得自动调用 `wewrite-visual`。没有封面时照常生成本地预览；只有用户另行要求
配图时才进入视觉模块。

本插件无 `wewrite run permission publish allow/deny` 路径：发布授权由 `wechat/prepareDraft` +
逐篇审批承担，Agent 不得自行记录权限。只排版、预览或「完整制作」都不能授予发布权限。

## 预检

- 标题存在且符合微信限制；摘要不超过 120 字节；正文 200-20000 字。
- 正文图片不超过 10 张且实际可读；表格不超过 4 列。
- 推送草稿必须有封面。没有封面不能假设微信会补默认图，改走本地预览。

## 动作

始终先生成本地预览：调用 `wewrite_preview`（kind=article 或 illustrated_article，expectedVersion
用真实哈希，themeId 用当前主题）。确认预览产物存在且通过兼容性校验。若没有明确推送要求，
流程到此结束；「写一篇」「完整制作」都不等于允许推送。

只有用户明确要求草稿箱时，走工作台 RPC：`wechat/prepareDraft` 只做本地预检，
`wechat/pushDraft` 转入宿主审批的操作位（pending_approval），由 S4 注册的真实推送工具逐篇确认。
Agent 不要执行任何发布 CLI、不要取令牌/上传/建草稿。

## 边界

- 本 stage 不注册 `wewrite_push_draft`，不真实推送微信。
- 禁止 Bash、通用文件写入（Write/Edit）、代码执行、委派；本 skill 只通过受控写作工具读写。
