# Tools Directory

## 目录结构

```
tools/
├── __init__.py
├── subagent.py          # 子代理工具
└── IMPLEMENTATION.md    # 实现文档
```

## 工作原理

Tools 是外部工具的封装，提供：
1. **子代理执行**: 通过 subagent.py 启动独立代理
2. **工具调用**: 集成外部 CLI 工具
3. **结果处理**: 标准化工具输出

### 子代理机制
```python
# subagent.py
class SubAgent:
    def spawn(task: str) -> Agent:
        """启动子代理"""
    
    def communicate(agent: Agent, message: str):
        """与子代理交互"""
```

## 解决的问题

- **复杂任务分解**: 将复杂任务交给子代理
- **工具集成**: 统一调用外部工具
- **结果标准化**: 工具输出转 SkillResult

## 配置方式

```yaml
tools:
  subagent:
    timeout: 300
    max_retries: 3
```

## 运行方式

### 单独使用
```python
from tools.subagent import SubAgent

agent = SubAgent.spawn("分析代码")
result = agent.communicate("请分析这段代码")
```

## 开发原则

### 1. 工具封装
- 提供简洁的 Python 接口
- 错误时返回标准错误格式

### 2. 子代理管理
- 资源清理
- 超时控制
- 状态跟踪

### 3. 文档要求
- 每个工具编写 IMPLEMENTATION.md
- 提供使用示例
