---
name: skill-mode-demo
description: 通过轻量 skill mode 分步骤完成需求澄清、草稿生成和最终交付
version: 1.0.0
owner: platform
triggers:
  - skill mode demo
  - 草稿助手
  - 需求澄清
tools: []
planning_mode: required
execution_style: stepwise
ask_user_policy: blocked_only
strategy:
  - "1. 分析用户目标与约束"
  - "2. 缺少关键信息时只问最小必要问题"
  - "3. 先给可执行草稿，再收敛为最终结果"
output_format: markdown
---

# Skill Mode Demo

这个 skill 用于验证轻量 skill session：
- 支持 [ASK_USER] 请求必要信息
- 用户补充后继续 [EXECUTE]
- 完成后输出 [FINISH]
