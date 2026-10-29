---
description: 选题与主题构思（从热榜与主题目录中选题并确定写作角度）
whenToUse: 需要从热榜/主题中选题并确定写作角度时
---

# 选题与主题构思

从工作台传入的主题目录（themeId）与热榜中选题，确定写作角度。

- 只读：可通过 `wewrite_run_detail` 查看当前 run 的主题/模式。
- 产出写入 `wewrite_artifact_write` kind=`brief` 与 kind=`claims`。
- 不执行 Bash/网络抓取/代码执行；热榜数据由工作台通过 RPC 提供。
