---
description: 正文写作流程（brief → claims → draft → article，全部经 wewrite_artifact_write 落盘，含任务书、主张与证据、素材与初稿）
whenToUse: 选题确定后开始正文写作时
---

# wewrite-write — 任务书、证据与初稿

## 前置

用 `wewrite_run_detail` 读取当前 run。本模块只写 `brief`、`claims` 和 `draft` 产物；
`article` 由审稿模块在通过后生成。没有 run 但用户给了选题时，由工作台创建 run；没有选题则转
`wewrite-topic`。

客户 style/persona 配置由工作台侧提供；人格只控制表达偏好，不能覆盖事实、个人材料和文章任务书。

进入时运行 `wewrite_run_step`（step=`write`，status=`in_progress`）。

## 3. 文章任务与素材

完整读取：

```text
读取: {skill_dir}/references/article-brief.md
读取: {skill_dir}/references/editorial-quality.md
读取: {skill_dir}/references/frameworks-quick.md
读取: {skill_dir}/references/content-enhance.md
```

### 3.1 先定义文章

明确目标读者、阅读情境、真正问题、读后收获、核心判断、新增价值、适用边界和最强反方。
从痛点、故事、清单、对比、热点解读、观点、复盘中选最适合的一种。把结果写入
`wewrite_artifact_write` kind=`brief`（YAML 对象，字段见 article-brief.md），不得直接跳到正文。

同时记录本次个人材料：只有用户在当前任务明确提供的经历、观察或原话才算可用。
`personal_materials.available=false` 时，可以写第一人称判断，禁止编造亲历、朋友同事、采访、
对话、时间地点、动作和感官场景。人格要求故事或私人开场时，改用观察、问题或判断开场。

### 3.2 建立主张与证据

围绕文章真正需要证明的 3-6 个主张搜索（只读 `WebSearch`/`WebFetch` 可保留）。每条进入文章的
具体数据、引述、案例或时效性事实，都要在原页面核对并立即记录来源账本：用
`wewrite_artifact_write`（kind=`sources`，YAML 对象）写入 `sources.yaml` 的来源列表——每条含
`id/title/publisher/url/published_at/claim/status`，`status` 为 `verified`/`unverified`/`user_provided`
三选一。优先原始报告、官方文档和当事方信息；用户材料标为 `user_provided`；不得把搜索摘要、
模型记忆或范文标为 `verified`；尤其不得把模型记忆标为 `verified`。

把事实、推断、意见和用户经历写入 `wewrite_artifact_write` kind=`claims`（YAML 对象），引用
`sources.yaml` 的来源编号。`unsupported` 的主张不得进入初稿。

搜索不可用时，只写不依赖最新数据的分析和有边界的判断；删除无法核实的数字、引述和
「研究显示」，并在任务中记录降级。

### 3.3 安全增强

按 `content-enhance.md` 为当前框架补足新增角度、行动条件、真实细节或决策标准。增强内容也
必须进入主张和来源清单。材料不足就缩小主张或换框架，不得强行反转、补故事或制造「内幕」。

## 4. 写初稿

范文库最多读取 2 篇相关范文：

- 标明 `ownership=user` 且为用户本人创作/修改的范文，可帮助校准声音和结构。
- 第三方或缺少元数据的旧范文一律按第三方处理，只能参考节奏与结构。
- 任何范文都不能提供可复用的个人经历、人物、对话、具体细节、观点或句子。

写作要求：

- 标题准确、有明确对象和利益点；正文通常 1200-2500 字，可按任务书调整。
- 开头尽快进入读者问题或核心判断；每一节服务 `brief.sections` 和对应 claim。
- 清楚区分事实、推断和个人意见；写出反方和适用边界，不用共识或套话凑字数。
- 账号声音来自 style、persona 和经过筛选的学习结果，但内容与证据永远优先。
- 禁止编造亲历、身份、采访、数字、评价、引用和来源中不存在的细节。

用 `wewrite_artifact_write`（kind=`draft`）落盘初稿。两种方式都要按任务书和主张清单通读一次，
修正归属、重复、跳跃和越界内容。完成后用 `wewrite_run_step`（step=`write`，status=`completed`）。

单独调用时告诉用户初稿、任务书和主张清单路径，并建议继续 `wewrite-review`；不要把初稿称为
已经审过的成稿。

## 硬约束

- 每次写入带 `requestId`（随机唯一），`runId` 恒为工作台传入的 runId。
- `article` 是候选终稿；写入后旧审稿立即失效，必须重新 review。
- 产物非空，UTF-8 字节不超过 2 MiB。
- 禁止 Bash、通用文件写入（Write/Edit）、代码执行、委派；本 skill 只通过受控写作工具读写。
