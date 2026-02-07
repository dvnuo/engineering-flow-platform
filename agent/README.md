# Agent Directory

## 目录结构

```
agent/
├── __init__.py
├── core.py              # Agent 核心逻辑
├── model_fallback.py    # 模型降级策略
├── llm.py              # LLM 接口封装
├── heartbeat/          # 心跳机制
└── fastlane/           # 快速通道
```

## 工作原理

### 1. Agent 核心流程
```
接收消息 → 理解意图 → 选择技能 → 执行 → 返回结果
```

### 2. Model Fallback 机制
```python
# model_fallback.py
class ModelFallback:
    def __init__(self):
        self.providers = ["openai", "anthropic", "ollama"]
    
    def get_model(self, task_type: str) -> str:
        # 根据任务类型选择模型
        # 失败时自动降级
```

### 3. LLM 接口封装
```python
# llm.py
class LLM:
    def complete(prompt: str, model: str = None) -> str:
        # 调用 LLM API
        # 支持多种 provider
```

## 解决问题

- **多模型支持**: OpenAI, Anthropic, Ollama 等
- **自动降级**: 主模型失败时切换备选
- **心跳机制**: 定期健康检查
- **快速响应**: fastlane 优先处理

## 配置方式

```yaml
# config.yaml
agent:
  default_model: "gpt-4"
  fallback_models:
    - "claude-3"
    - "ollama/mistral"
  heartbeat_interval: 300
```

## 运行方式

### 启动 Agent
```bash
python main.py
```

### 测试 Agent
```bash
pytest tests/test_agent*.py -v
```

## 开发原则

### 1. 模块职责
- `core.py`: 主逻辑和协调
- `model_fallback.py`: 模型选择和降级
- `llm.py`: LLM API 调用
- `heartbeat/`: 监控机制

### 2. 错误处理
- 静默降级
- 重试机制
- 日志记录

### 3. 性能优化
- 缓存模型响应
- 连接池管理
- 并发请求
