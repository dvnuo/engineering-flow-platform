# Memory Directory

## 目录结构

```
memory/
├── __init__.py
└── (memory storage implementations)
```

## 工作原理

持久化内存系统：
1. **短期记忆**: 当前对话上下文
2. **长期记忆**: 跨会话的重要信息
3. **语义搜索**: 基于内容的快速检索
4. **数据持久**: SQLite/文件存储

## 解决的问题

- **知识积累**: 跨会话学习
- **快速检索**: 语义搜索能力
- **数据持久**: 防止数据丢失
- **上下文管理**: 维护对话历史

## 配置方式

```yaml
memory:
  type: "sqlite"        # sqlite/file
  path: "./memory.db"
  max_entries: 10000
  search_limit: 100
```

## 运行方式

### 初始化存储
```python
from memory import MemoryStore

store = MemoryStore()
store.init()
```

### 保存记忆
```python
store.save("user_preference", {"theme": "dark"})
```

### 搜索记忆
```python
results = store.search("user preferences")
```

## 开发原则

### 1. 数据结构
```python
class Memory:
    key: str           # 记忆键
    value: str         # 记忆内容
    timestamp: datetime # 时间戳
    tags: List[str]    # 标签
```

### 2. 检索策略
- 关键词匹配
- 语义相似度
- 时间衰减

### 3. 性能优化
- 索引加速
- 分页加载
- 增量同步
