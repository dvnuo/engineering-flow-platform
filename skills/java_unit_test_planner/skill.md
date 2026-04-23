---
name: java-unit-test-planner
description: 根据类/方法需求生成 Java 单元测试设计思路（case/mocks/edge/exception）
version: 1.0.0
owner: dev-qa-collab
triggers:
  - java unit test
  - unit test planner
  - 生成 java 单元测试思路
  - java 测试设计
tools: []
planning_mode: required
execution_style: stepwise
ask_user_policy: blocked_only
strategy:
  - "1. 先确定被测对象职责、输入输出、依赖与异常路径。"
  - "2. 缺少方法签名或关键业务规则时 ASK_USER。"
  - "3. 每轮推进一个重点：normal path、edge case、exception、mock strategy。"
  - "4. 结果以测试设计为主，不强制展开完整实现代码。"
output_format: markdown
---

# Java Unit Test Planner

用于开发与测试协作阶段快速形成 Java 单元测试计划。

## Skill Mode 推进方式
- 信息缺口优先 **[ASK_USER]**。
- 明确后逐步 **[EXECUTE]** 输出测试设计。
- 覆盖完整后 **[FINISH]** 给出优先级与实施建议。

## 输出建议
- Test Cases 列表
- Mock Points
- Edge Cases
- Exception Paths
- 优先级与重构建议
