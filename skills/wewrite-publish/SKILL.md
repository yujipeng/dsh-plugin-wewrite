---
description: 预览与请求草稿推送（S3 只到预览 + 预检 + 发请求，不真实推送到微信）
whenToUse: 工作台完成审稿后需要预览或发起逐篇确认推送请求时
---

# 预览与请求草稿推送

本 stage **不注册 `wewrite_push_draft`**，不真实推送微信。

- 预览：调用 `wewrite_preview`（kind=article 或 illustrated_article，expectedVersion 用真实哈希，themeId 用当前主题）。
- 请求草稿推送：工作台 RPC `wechat/prepareDraft` 只做本地预检，`wechat/pushDraft` 转入宿主审批的操作位（pending_approval），由 S4 注册真实推送工具后逐篇确认。
- 不要执行任何发布 CLI、不要取令牌/上传/建草稿。
