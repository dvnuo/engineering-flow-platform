---
name: java-cucumber-generator
description: 将业务需求转成 Java Cucumber 测试草稿（feature + step definitions + 结构建议）
version: 1.0.0
owner: qa-platform
triggers:
  - java cucumber
  - cucumber java
  - generate cucumber for java
  - 生成 java cucumber
tools: []
planning_mode: required
execution_style: stepwise
ask_user_policy: blocked_only
strategy:
  - "1. 先识别业务目标、角色、关键规则；信息不足时先 ASK_USER。"
  - "2. 每轮只推进一个有价值小步骤：例如先给 feature 草稿，再给 step definitions 骨架。"
  - "3. 简单需求可一次 EXECUTE；复杂需求分多轮推进，最终 FINISH 总结可落地清单。"
  - "4. 不要一次性铺开过多实现细节；优先补齐验收规则与边界行为。"
output_format: markdown
---

# Java Cucumber Generator

用于把业务场景需求转成 Java 技术栈下可落地的 Cucumber 测试草稿。

## Skill Mode 推进方式
- 缺少关键信息（角色、业务规则、成功/失败条件）时先 **[ASK_USER]**。
- 信息足够时执行当前最关键一步并 **[EXECUTE]**。
- 任务完成后给出结果总结并 **[FINISH]**。

## 建议输出内容
- Feature file 草稿（Feature / Scenario / Scenario Outline）
- Java step definitions 草稿（Given/When/Then 方法骨架）
- Page/Service class 组织建议
- 最小可执行测试结构建议

## 工具使用原则
- 默认先用用户输入推进。
- 只有在明确有帮助时才使用工具（例如读取已有项目约定）。
- 不要为“可能有用”而滥用工具。
