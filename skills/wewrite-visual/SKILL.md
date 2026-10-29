---
description: 可选视觉流程（默认 visualMode=none，不启用）
whenToUse: 仅当工作台传入 visualMode 为 cover/full/prompts 时按需启用
---

# 视觉流程（可选）

默认 `visualMode=none`，本 stage 不启用生图。

- 只有在工作台传入的 `visualMode` 非 none 时才考虑视觉产物（image_prompts/images_manifest）。
- 不触发生图 provider，不产生真实图片资产；封面由工作台上传，不能因缺封面自动生图。
