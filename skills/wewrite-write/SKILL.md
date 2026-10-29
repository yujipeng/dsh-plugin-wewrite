---
description: 正文写作流程（brief → claims → draft → article，全部经 wewrite_artifact_write 落盘）
whenToUse: 选题确定后开始正文写作时
---

# 正文写作流程

按 brief → claims → draft → article 顺序写作，全部经 `wewrite_artifact_write` 落盘。

- 每个 run 只能写自己的 runId；写入带 `requestId`，kind 决定文件路径。
- article 是候选终稿；写入后旧审稿立即失效，必须重新 review。
- 产物非空，UTF-8 字节不超过 2 MiB。
- 禁止 Bash/通用文件写入/代码执行/委派。
