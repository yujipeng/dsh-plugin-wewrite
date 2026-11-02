---
description: 可选视觉流程（默认 visualMode=none；cover/full/prompts 按需启用，生成封面与内文配图或只交付提示词）
whenToUse: 仅当工作台传入 visualMode 为 cover/full/prompts 时按需启用
---

# wewrite-visual — 封面与配图

## 前置

用 `wewrite_run_detail` 读取当前 run 的 `article`、`illustrated_article`、`image_prompts`、
`images_manifest`、`visual` 和任务目录。已完成的文章也可以直接配图，不要恢复或新建写作任务。
封面由工作台上传，本 skill 不触发生图 provider，不能因缺封面自动生图。

完整读取：

```text
读取: {skill_dir}/references/visual-guide.md
```

根据本轮原话确定模式：只要封面用 `cover`，封面和必要内文图用 `full`，只要提示词用 `prompts`。
不要沿用写作模式猜测用户想生图。`prompts` 或 `skip_image_gen=true` 时仍要完成提示词，但不调用
付费图片服务。

## 执行

1. 从终稿提取 3-5 个具体实体和文章主情绪，形成统一色板、构图与质感。
2. 写一个封面提示词。`cover` 模式到此为止；`full` 模式只为确实需要解释或缓冲阅读的
   段落补图，不按固定数量凑图。
3. 将提示词写到 `wewrite_artifact_write` kind=`image_prompts`；批量清单写到 kind=`images_manifest`
   （JSON 对象）。
4. 数量必须不超过任务的 `visual.max_images`。本插件不注册生图工具，不调用付费图片服务；
   需要实际图片时由工作台上传，Agent 不得绕过上限或自行生成。
5. 有任务时 `wewrite_run_step`（step=`visual`，status=`completed`）。

只有提示词时同样标 completed，并明确说明未产生费用、没有实际图片。

## 边界

- 禁止 Bash、通用文件写入（Write/Edit）、代码执行、委派；本 skill 只通过受控写作工具读写。
- 不触发生图 provider；不产生真实图片资产。
