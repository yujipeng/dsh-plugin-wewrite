---
description: WeWrite 风格设置模块：首次使用引导（onboard）+ 重设公众号写作风格配置（style.yaml）。触发关键词：重新设置风格、修改风格配置、设置公众号风格、公众号首次设置、换写作人格、改排版主题偏好。也由主入口 wewrite 在缺 style.yaml 时自动激活。不应被通用的「改文风」「润色」触发——那是编辑动作，不是配置动作。
whenToUse: 需要引导用户设置或修改写作风格配置（style.yaml）时
---

# wewrite-style — 风格设置 / Onboard

## 运行约定

- **受控工具**：确定性操作走 `wewrite_style_get` / `wewrite_style_validate` 读与校验账号级
  style 配置；style.yaml 的写入由工作台「设置」面板（读 style.get → 编辑 → style.put 保存）
  完成。Agent 不直接写 style.yaml，也没有文件写入能力。
- **{home}**：用户状态目录（工作台数据根）。style/lessons/exemplars 全在 {home}，不在仓库；
  references 文档中的状态路径同此约定。
- **`读取: <路径>`** = 用 `wewrite_style_get` 或随包 references 真实读完该文件再继续，不是注释。
- **references/**：本 skill 自带 `{skill_dir}/references/`；references 文档内的 `{skill_dir}` 即本 skill 目录。
- 本模块是**交互式**的（需要问用户问题），不受主管道「全自动」约束；但在受控工作流里没有稳定的
  多轮问答入口，收集问题通过工作台「设置」面板完成，Agent 不直接逐题追问用户。

## 执行

**触发场景**：
1. 主入口 Step 1 发现 `{home}/style.yaml` 不存在（首次使用）
2. 用户说「重新设置风格」「修改配置」

两种场景都执行：

```
读取: {skill_dir}/references/onboard.md
```

按 onboard.md 的 Phase 1-4 完成：

- **Phase 1（收集信息）**：把 onboard.md 的必问/选问字段整理成一份引导说明，指引用户到工作台
  「设置 → 写作风格」区块填写并保存（面板内部经 `style.put` 落盘）。Agent 侧可用
  `wewrite_style_get` 确认保存结果。
- **Phase 2（生成配置）**：配置由面板保存生成 `{home}/style.yaml`；Agent 用
  `wewrite_style_get` 读回全文展示给用户，并说明「这个配置 OK 吗？有什么要改的？」。
- **Phase 3（Playbook）**：可选，不阻断。按 onboard.md 说明指引用户把语料放入 `{home}/corpus/`
  并在工作台提交；本批不提供 build-playbook 命令，语料学习后续批次补。
- **Phase 4（试跑）**：按 onboard.md 询问是否现在试写一篇。

重设风格时以现有 style.yaml 为基线（`wewrite_style_get` 读回），只改用户要改的字段，
改完展示全文让用户确认。

**完成后**：若由主管道激活（首次 onboard），回到主管道 Step 1 继续；若用户单独触发，
告知「配置已更新，下次写作自动生效」。

## 边界

- 不执行 shell、网络抓取、代码执行、委派；style.yaml 的写入只发生在工作台面板（style.put）。
- `wewrite_style_put` 工具已注册，但**是账号级配置，只有 owner 可改**——Agent 调用一律返回
  `FORBIDDEN`（中文提示「账号级配置仅 owner 可改，请在工作台设置面板操作」），不能代写；
  Agent 只读走 `wewrite_style_get` / `wewrite_style_validate`。
