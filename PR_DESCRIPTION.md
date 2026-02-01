## 功能概述

实现了 OpenClaw Mini 的工具调用功能，支持 Agent 执行 Shell 命令、读写文件、网页搜索等操作。

## 主要变更

### 1. LLM 客户端工具调用支持
- `agent/llm.py`: 支持 `tools` 参数，实现 OpenAI Function Calling
- 返回值从纯文本改为 Dict，包含 `content` 和 `tool_calls`
- 添加模型兼容性检测 (gpt-3.5-turbo 不支持 tools)

### 2. Agent ReAct 模式实现
- `agent/core.py`: 实现 ReAct (Reasoning + Acting) 模式
- 流程: 用户 → LLM(带工具) → 工具调用 → 执行 → 结果 → LLM → 最终回复
- 遵循 OpenClaw 的 Agent Loop 设计

### 3. 核心工具集
- `skills/executor/tools.py`: 实现 7 个核心工具
  - `exec`: 执行 Shell 命令
  - `read`: 读取文件
  - `write`: 写入文件
  - `edit`: 编辑文件
  - `web_search`: 网页搜索 (Brave API)
  - `web_fetch`: 获取网页内容
  - `image`: 分析图片

### 4. 工具 Schema 格式修复
- 修复 OpenAI API 要求的 tool calling schema 格式
- 添加 `type: object` 和 `required` 字段
- 修复 `tool` role 消息必须紧跟 `tool_calls` 的问题

## 使用示例

```
用户: 运行 ls -la
Bot → exec("ls -la") → 返回文件列表

用户: 读取 config.yaml
Bot → read("config.yaml") → 返回文件内容

用户: 搜索 Python 教程
Bot → web_search("Python 教程") → 返回搜索结果
```

## 测试验证

已在 Discord 频道测试通过，Agent 能正确调用工具并返回结果。

## Breaking Changes

无

## 相关文档

参考 OpenClaw 官方文档: https://docs.openclaw.ai/concepts/agent-loop
