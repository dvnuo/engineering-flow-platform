---
name: jira-to-cucumber-tests
description: 基于 Jira issue 生成 Cucumber 场景草稿，并在信息不足时最小化提问
version: 1.0.0
owner: qa-platform
triggers:
  - jira cucumber
  - issue to cucumber
  - jira to cucumber
  - 根据 jira 生成 cucumber
tools:
  - jira_get_issue
  - jira_search
planning_mode: required
execution_style: stepwise
ask_user_policy: blocked_only
strategy:
  - "1. 优先提取 Jira issue 的 acceptance criteria、业务流程和限制条件。"
  - "2. 若 issue 信息不足，不要过度猜测；先 ASK_USER 补关键验收条件。"
  - "3. 每轮推进一个步骤：先理解 issue，再产出 scenario 草稿，最后 FINISH。"
  - "4. 工具仅在明确需要获取 issue 信息时使用，不做投机式查询。"
output_format: markdown
---

# Jira to Cucumber Tests

用于将 Jira issue 转换为可评审的 Cucumber 测试草稿。

## Skill Mode 推进方式
- 优先尝试从 issue key / 链接中读取需求上下文。
- 若验收条件缺失，先 **[ASK_USER]** 获取最小必要信息。
- 先产出 scenario 列表，再补 feature 草稿，最后 **[FINISH]**。

## 建议输出
- 关键验收标准摘要
- Gherkin Scenarios（happy/negative/boundary）
- 可直接评审的 feature 草稿

## 工具使用原则
- `jira_get_issue` / `jira_search` 仅在明确可推进当前步骤时使用。
- 缺业务规则时优先问用户，不要乱查大量 issue。
