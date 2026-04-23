---
name: test-scope-clarifier
description: 在模糊需求下先澄清再输出测试范围（in/out scope、假设、开放问题）
version: 1.0.0
owner: qa-platform
triggers:
  - clarify test scope
  - test scope
  - 需求澄清测试范围
  - 测试范围梳理
tools: []
planning_mode: required
execution_style: stepwise
ask_user_policy: blocked_only
strategy:
  - "1. 面对模糊需求，先识别不确定项并 ASK_USER 最小必要问题。"
  - "2. 每轮只推进一个澄清点，避免一次提太多问题。"
  - "3. 逐步沉淀 in scope / out of scope / assumptions / open questions。"
  - "4. 最终 FINISH 给出可执行测试范围建议。"
output_format: markdown
---

# Test Scope Clarifier

这是一个高频多轮协作 skill，用于把“模糊需求”转成可执行测试范围。

## Skill Mode 推进方式
- 先澄清，不急于输出最终方案。
- 缺关键业务输入时必须 **[ASK_USER]**。
- 每轮收敛一个关键不确定点并 **[EXECUTE]**。
- 收敛后 **[FINISH]** 给出结构化范围建议。

## 建议输出
- In Scope
- Out of Scope
- Assumptions
- Open Questions
- Suggested Test Areas
