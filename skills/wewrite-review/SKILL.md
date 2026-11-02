---
description: 审稿流程（五维评估 → wewrite_review → 通过后 wewrite_run_finish，含事实核对、改稿与成稿门禁）
whenToUse: 正文写完后需要审稿并通过门禁时
---

# wewrite-review — 编辑、改稿与成稿

## 前置

用 `wewrite_run_detail` 读取当前 run，优先读取 `draft`，并读取 `brief`、`claims` 和 `sources`。
旧任务没有初稿产物时才回退 `article`。管道内先运行 `wewrite_run_step`（step=`review`，status=`in_progress`）。

完整读取：

```text
读取: {skill_dir}/../wewrite-write/references/article-brief.md
读取: {skill_dir}/../wewrite-write/references/editorial-quality.md
读取: {skill_dir}/references/seo-rules.md
```

用户只说「检查一下」且给了外部文件时，只给报告；主流程、任务内初稿或用户明确说「优化」时，
必须直接完成必要修改。

## 审稿与改稿

### 1. 对齐任务

检查文章是否真的回答 `audience.question`，核心判断是否与 `thesis` 一致，每节是否推动指定
claim，结尾是否交付 `goal.takeaway/action`。无关段落删除，不用漂亮结构掩盖答非所问。

### 2. 核对事实与个人材料

读回 `sources` 账本，把正文中的具体数字、日期、引述、研究结论和时效性事实逐项对到
`claims` 与原始来源。来源不支持时优先补查；查不到就删除、缩小或改成明确的推断。
不能用模型记忆补洞。

检查所有第一人称事件、朋友同事、采访、对话、时间地点和感官细节。它们必须来自本次任务
明确记录的用户材料；否则属于阻断问题，直接删除或改成非亲历论述。

### 3. 五项编辑判断

按「准确、观点、有用、合声、好读」各评 1-5 分，并列出阻断问题和最多 5 个主要问题：

- `pass`：平均分至少 4、单项不低于 3、没有阻断问题。
- `revise`：能在现有材料内修正。直接修改后重新执行 1-3，不得只写建议。
- `needs_input`：仅限用户明确要求个人故事而材料不足且无法安全换框架。

最多两轮。第二轮仍有问题时，删掉不可靠内容、缩小承诺，生成能通过的可靠版本；不得给未
通过的文章贴上「可交付」。通过后把最终正文写入 `article`。

### 4. 标题、摘要与工具提示

生成一个主标题、两个准确的备选标题、40 字内摘要和 3-5 个标签。关键词自然出现，不按密度
硬塞，也不为打开率虚构数字或承诺。审稿不调用配图。

## 保存编辑报告

把编辑判断构造为 assessment 对象，用真实哈希调用 `wewrite_review`：

```json
{
  "decision": "pass",
  "pass_number": 1,
  "dimensions": { "accuracy": 4, "viewpoint": 4, "usefulness": 4, "voice": 4, "readability": 4 },
  "blockers": [],
  "major_issues": [],
  "notes": ""
}
```

`wewrite_review` 会复用上游 `build_report` 生成报告并落盘 `review_report`，只有报告里的
`publishable=true` 且 decision=`pass` 才能完成审稿。通过后用 `wewrite_run_step`（step=`review`，
status=`completed`），再交给主流程 `wewrite_run_finish` 封存。

## 硬约束

- 读回 article 与 draft 的实际版本哈希（`wewrite_artifact_get` 返回 version），用真实哈希调用
  `wewrite_review`；expectedVersion/reportVersion 一律真实哈希。
- 不能伪造 pass；不能绕过 finish 直接封存；revise/needs_input 时修改 article 后重新审稿。
- 禁止 Bash、通用文件写入（Write/Edit）、代码执行、委派；本 skill 只通过受控写作工具读写。
