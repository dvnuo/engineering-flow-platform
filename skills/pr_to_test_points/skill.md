---
name: pr-to-test-points
description: 根据 PR 描述或变更内容提炼测试点与回归建议
version: 1.0.0
owner: qa-platform
triggers:
  - pr test points
  - pull request test
  - 根据 pr 生成测试点
  - 代码变更测试点
tools: []
planning_mode: required
execution_style: stepwise
ask_user_policy: blocked_only
strategy:
  - "1. 识别 PR 目标、改动范围、风险路径；必要时使用工具读取差异。"
  - "2. 信息不足时 ASK_USER（如发布范围、关键业务影响）。"
  - "3. 输出最小可执行测试点与回归建议，最后 FINISH。"
output_format: markdown
---

# PR to Test Points

用于把代码变更快速转成测试执行清单，适合提测前评审。

## Skill Mode 特点
- 可通过工具读取 PR/变更上下文。
- 若业务背景缺失先 **[ASK_USER]**。
- 每轮产出一个清晰测试增量并 **[EXECUTE]**。
