---
name: feature-stepdef-scaffolder
description: 基于已有 Gherkin feature 补全 step definition 设计骨架
version: 1.0.0
owner: qa-platform
triggers:
  - step definition scaffold
  - feature to step defs
  - 根据 feature 补 step definitions
  - cucumber step 骨架
tools: []
planning_mode: required
execution_style: stepwise
ask_user_policy: blocked_only
strategy:
  - "1. 先解析现有 feature 场景与参数。"
  - "2. 缺少技术约定（测试框架、目录、命名）时 ASK_USER。"
  - "3. 分轮输出 step definitions 骨架与组织建议。"
  - "4. 避免一次性生成大量低质量样板代码。"
output_format: markdown
---

# Feature StepDef Scaffolder

用于已有 feature 文件时快速生成 step definition 设计骨架。

## Skill Mode 推进方式
- 输入已有 feature 文本或路径。
- 缺技术约定先 **[ASK_USER]**。
- 按场景逐步 **[EXECUTE]** 输出 step 结构。
- 收敛后 **[FINISH]** 给出落地建议。
