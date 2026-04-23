---
name: jira-to-manual-test-cases
description: 根据 Jira issue 生成人工测试用例（preconditions/steps/expected/negative）
version: 1.0.0
owner: qa-platform
triggers:
  - jira manual test
  - issue to manual test
  - jira test case
  - 根据 jira 生成人工测试
tools:
  - jira_get_issue
  - jira_search
planning_mode: required
execution_style: stepwise
ask_user_policy: blocked_only
strategy:
  - "1. 先提取 issue 的目标、验收标准、依赖条件。"
  - "2. 信息不足时 ASK_USER 补关键前置条件与判定标准。"
  - "3. 生成结构化人工测试：preconditions、steps、expected、negative。"
  - "4. 每轮只推进一小步，避免一次输出过长不可评审内容。"
output_format: markdown
---

# Jira to Manual Test Cases

用于生成偏 QA 执行视角的人工测试用例，而非自动化代码。

## Skill Mode 推进方式
- 先确认测试目标和范围。
- 若信息不足，先 **[ASK_USER]**。
- 逐步给出可执行的人工测试用例草稿并 **[EXECUTE]**。
- 完整后汇总测试清单并 **[FINISH]**。

## 输出建议
- Preconditions
- Test Steps
- Expected Results
- Negative/异常路径
- 执行优先级建议
