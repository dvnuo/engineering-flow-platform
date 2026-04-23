---
name: api-test-scenario-generator
description: 基于接口需求与规则生成 API 测试场景（happy/validation/error/boundary）
version: 1.0.0
owner: qa-platform
triggers:
  - api test scenario
  - generate api tests
  - 接口测试场景
  - 生成 api 测试
tools: []
planning_mode: required
execution_style: stepwise
ask_user_policy: blocked_only
strategy:
  - "1. 识别接口路径、请求字段、响应结构、状态码与业务规则。"
  - "2. 缺关键字段或判定规则时先 ASK_USER，再继续生成。"
  - "3. 每轮推进一个核心步骤：先 happy path，再补 validation/error/boundary。"
  - "4. 输出以可执行测试思路为主，不陷入实现细节。"
output_format: markdown
---

# API Test Scenario Generator

用于在需求早期快速形成 API 测试覆盖草稿。

## Skill Mode 推进方式
- 缺请求/响应关键定义时优先 **[ASK_USER]**。
- 信息充分后 **[EXECUTE]** 当前最有价值测试分组。
- 覆盖完整后 **[FINISH]** 汇总。

## 结果建议结构
- Happy Path
- Validation Cases
- Error Cases
- Boundary Cases
- 风险与待确认项
