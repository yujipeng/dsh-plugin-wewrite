---
description: 审稿流程（五维评估 → wewrite_review → 通过后 wewrite_run_finish）
whenToUse: 正文写完后需要审稿并通过门禁时
---

# 审稿流程

- 读回 article 与 draft 的实际版本哈希（`wewrite_artifact_get` 返回 version），用真实哈希调用 `wewrite_review`。
- 评估五维：accuracy / viewpoint / usefulness / voice / readability（各 1–5 分），decision 为 pass/revise/needs_input，pass_number 为 1 或 2。
- 报告 `publishable:true` 且 decision=`pass` 才调用 `wewrite_run_finish`（expectedVersion/reportVersion 用真实哈希）。
- revise/needs_input 时修改 article 后重新审稿。
- 不能伪造 pass；不能绕过 finish 直接封存。
