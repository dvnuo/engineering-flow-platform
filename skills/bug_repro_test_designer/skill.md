---
name: bug-repro-test-designer
description: 从缺陷描述生成复现路径、验证点和回归测试草稿
version: 1.0.0
owner: qa-platform
triggers:
  - bug repro test
  - defect test design
  - 缺陷复现场景
  - 生成缺陷回归测试
tools: []
planning_mode: required
execution_style: stepwise
ask_user_policy: blocked_only
strategy:
  - "1. 先提炼复现前置条件、触发步骤、实际结果与期望结果。"
  - "2. 缺少关键复现条件时 ASK_USER，不盲猜环境与数据。"
  - "3. 每轮推进一个目标：先复现，再验证修复，再补回归范围。"
  - "4. 最终 FINISH 给出可执行缺陷验证清单。"
output_format: markdown
---

# Bug Repro Test Designer

用于在缺陷分析与回归阶段快速形成测试行动方案。

## Skill Mode 推进方式
- 缺复现条件时先 **[ASK_USER]**。
- 信息足够后 **[EXECUTE]** 当前关键验证步骤。
- 最终 **[FINISH]** 输出复现 + 回归清单。
