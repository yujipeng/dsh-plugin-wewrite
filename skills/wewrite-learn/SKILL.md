---
description: WeWrite 自学习模块：从用户的人工修改中学习写作偏好（playbook 飞轮）、导入范文建风格库、从公众号文章学习排版主题。触发关键词：学习我的修改、我改了学习一下、导入范文、学习这篇文章、查看范文库、学习排版、学排版。不应被通用的「学习」「总结这篇文章」触发——需要公众号/WeWrite 上下文。
whenToUse: 需要记录改稿学习或查看范文库时
---

# wewrite-learn — 自学习（改稿飞轮 / 范文库 / 排版学习）

## 运行约定

- **受控工具**：本批提供 `wewrite_learn_list`（列出改稿学习条目）与
  `wewrite_exemplar_list`（列出范文库）两个只读工具。改稿记录（learn.record）与范文入库
  （exemplar.put）由工作台在「从编辑器保存时记录差异」写入，Agent 没有文件写入能力。
- **{home}**：用户状态目录（工作台数据根）。lessons/exemplars 全在 {home}，不在仓库；
  references 文档中的状态路径同此约定。
- **`读取: <路径>`** = 用受控工具或随包 references 真实读完该文件再继续，不是注释。
- **references/**：本 skill 自带 `{skill_dir}/references/`；references 文档内的 `{skill_dir}` 即本 skill 目录。

## 子功能分发

| 用户说 | 动作 |
|--------|------|
| 学习我的修改 / 我改了，学习一下 | `读取: {skill_dir}/references/learn-edits.md`，按其流程执行。**本批只支持本地 markdown 修改**（在工作台编辑器保存时由工作台记录差异为 lesson）；`--from-wechat` 同步草稿箱属后续批次。 |
| 学习排版 / 学排版 + URL | 本批不做（需 URL 抓取，归后续批次）。 |
| 学习这篇文章 / 导入范文 + URL | 本批不做（需网络抓取，归后续批次）；本地范文经工作台「范文库」入口以 `exemplar.put` 入库。 |
| 导入范文 + 本地文件 | 在工作台「范文库」入口以 `exemplar.put` 入库；用户明确为本人文章时标 `ownership=user`、`authenticity=user_edited`。 |
| 查看范文库 | `wewrite_exemplar_list` |

**范文库的用途**：exemplars 会在写作模块（wewrite-write）按框架类型注入初稿 prompt，
只用于校准结构和节奏，不能复用其中的观点、句子或个人经历。查看后告知用户库里现有
多少篇、覆盖哪些 category 和所有权标记。

**改稿飞轮的价值**：每次学习让下一篇初稿更接近用户风格。learn-edits.md 的
只有重复出现或用户明确确认的同范围规则才会成为硬约束；单次修改始终只是软参考。

## 边界

- 不执行 shell、网络抓取、代码执行、委派；lessons/exemplars 的写入只发生在工作台
  （learn.record / exemplar.put 为 owner 账号级写操作），Agent 工具只有只读入口。
