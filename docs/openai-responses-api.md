# OpenAI GPT-5 Model Support

## 目标

将默认模型从 gpt-3.5-turbo 升级到 gpt-5-mini。

## 改动范围

### 1. 后端 (src/agents/llm.py)
- 默认模型改为 gpt-5-mini

### 2. 前端设置页面
- 移除旧模型（GPT-4 及以下）
- 显示新模型选项：GPT-5 Mini, GPT-5, GPT-4o

## 注意

- gpt-5-mini 和 gpt-5 可能需要 OpenAI API 许可才能使用
- 如果模型不可用，API 会返回错误
