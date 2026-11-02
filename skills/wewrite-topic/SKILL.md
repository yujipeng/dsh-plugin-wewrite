---
description: 选题与主题构思（从工作台热榜与主题目录中选题，按 topic-selection 规则评分并确定写作角度）
whenToUse: 需要从热榜/主题中选题并确定写作角度时
---

# wewrite-topic — 选题

## 运行约定

- **受控工具**：确定性操作走 `wewrite_run_detail` 读当前 run 的主题/模式；热榜数据由工作台
  RPC（`hotspots/fetch`）提供，Agent 不自行抓取网络。
- **`读取: <路径>`** = 用 `wewrite_artifact_get` 或随包 references 真实读完该文件再继续，不是注释。
- **references/**：本 skill 自带 `{skill_dir}/references/`；references 文档内的 `{skill_dir}` 即本 skill 目录。
- **任务状态**：通过 `wewrite_run_step` 推进步骤状态，通过 `wewrite_artifact_write` 落盘产物；不直接改状态文件。

## 前置

工作台已传入主题目录（themeId）与热榜（RPC 提供）。没有主题上下文时，问用户「你的公众号主要写哪几个方向？」，
用回答临时代替 topics。客户 topics、content_style 由工作台侧 style 配置提供。

## 2.1 热点与高频需求参考

- 热点列表由工作台 `hotspots/fetch` 提供（JSON：title/source/hot/url/description）。
- 高频需求参考（可选）：由工作台提供近一周同垂类文章列表（标题/摘要/发布时间/账号）。
  它只能说明同题出现频率，不能声称文章「爆款」。

## 2.2 历史分析与去重

工作台传入的历史表现数据（如有 stats：阅读量/分享量）用于加权：

- 统计哪种 `framework` 的文章表现最好 → 推荐框架时加权。
- 统计哪种 `enhance_strategy` 的表现最好 → 增强策略选择时参考。
- 近 7 天已写的关键词降分（去重）。

无 stats 时跳过效果分析，仅做去重。

## 2.3 生成选题

```
读取: {skill_dir}/references/topic-selection.md
```

生成 **10 个选题**，其中：

- **5-6 个热点选题**：基于 2.1 的热点，按 topic-selection.md 规则评分。
- **2-3 个高频角度选题**（仅 2.1 有产出时）：基于近一周同题文章，标注「高频需求」；无产出时份额还给热点。
- **2-3 个常青选题**：不依赖热点，从客户 topics 领域生成长尾内容（教程/方法论/经验总结/工具推荐），标注「常青」。

每个选题含标题、评分、点击率潜力、SEO 友好度、推荐框架。自动模式选最高分；交互模式展示全部等用户选。

## 完成

用 `wewrite_artifact_write`（kind=`brief`）落盘任务书与选题结论，再用 `wewrite_run_step` 把
`topic` 置 `completed`。单独激活且用户只要选题列表时，展示 10 个选题即可；用户选定后写入状态并提示
「可以直接说“就写这个”进入写作」。

## 边界

- 不执行 shell、网络抓取、代码执行、委派；热榜/SEO/历史数据均由工作台 RPC 或随包 references 提供。
- 选题质量优先：不要只挑热度最高的，相关度与切入价值同等重要；每个选题必须配拟好的标题（20-28 字），不是热点原标题。
