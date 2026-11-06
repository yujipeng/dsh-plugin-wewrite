---
description: WeWrite 多平台改写模块：把一篇公众号文章（或指定源稿）内容级真改写成其他平台版本，当前支持小红书（图文笔记）、抖音（口播脚本），检查编辑质量与源稿相似度。触发关键词：改写成小红书、小红书版、抖音版、口播稿、多平台分发、一稿多发、平台改写。不应被"翻译"、"缩写"、"换个标题"触发——那些是单平台编辑动作。
whenToUse: 需要把公众号文章改写成小红书/抖音等其他平台版本时
---

# wewrite-rewrite — 一源多平台改写

## 运行约定

- **受控工具**：确定性操作走 `wewrite_run_detail` / `wewrite_artifact_get`（读当前 run 的正文源稿）、
  `wewrite_rewrite_similarity`（源稿相似度门）、`wewrite_rewrite_score`（机械语言风险打分）。
  改写产物在最终回复中给出，**不写文件、不修改源稿**（本插件没有通用文件写入能力）。
- **{home}**：用户状态目录（工作台数据根）。config/style/history/playbook/output/exemplars 全在
  {home}，不在仓库；references 文档中的状态路径同此约定。
- **`读取: <路径>`** = 用受控工具或随包 references 真实读完该文件再继续，不是注释。
- **references/**：本 skill 自带 `{skill_dir}/references/` 与 `{skill_dir}/platforms/`；
  references 文档内的 `{skill_dir}` 即本 skill 目录。

## 前置

1. **源文章**：按优先级解析——用户粘贴的内容 > 当前绑定 run 的 `article` 产物（用
   `wewrite_artifact_get`，kind=`article` 读取）。都没有 → 问用户「要改写哪篇？直接粘贴全文」。
   源稿只读，绝不在本流程中被改写或覆盖。
2. **目标平台**：用户点名了就用；没点名 → 列出 `{skill_dir}/platforms/` 下的可用平台
   （当前：xiaohongshu 小红书、douyin 抖音）问用户要哪几个，或「全部」。

## 执行

```
读取: {skill_dir}/references/multiplatform-rewrite.md
```

对每个目标平台：

1. `读取: {skill_dir}/platforms/<id>.yaml`，按其 `rewrite_brief`、字数区间、标签数、
   输出格式改写。
2. 遵守 multiplatform-rewrite.md 的原创铁律（内容级真改，重构信息顺序/开头/表达，
   不是洗稿）与人设一致要求（persona 内核不变，只适配表达方式）。
3. 过双质量门（multiplatform-rewrite.md「质量门」小节）：
   - `wewrite_rewrite_score`（text=该版本全文）→ `quality_score` 建议 ≥ 60；
   - `wewrite_rewrite_similarity`（source=源稿全文，variants=[该版本全文]）→
     `max_similarity` 应 ≤ 0.6；
   不过就重写该版本，最多 2 次；仍不过则保留最好的一版并说明。

## 完成

在最终回复中给出每个平台版本的产出（标题、正文全文或可核对的完整段落）、字数、双质量门
结果；小红书版说明配图情况（复用了源稿哪几张图，或「需补图」）。提醒：各平台账号发布是
用户手动动作，WeWrite 当前只发公众号草稿箱。
