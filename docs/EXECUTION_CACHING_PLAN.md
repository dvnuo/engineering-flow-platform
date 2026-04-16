# Engineering Flow Platform vs CodeW 执行与缓存能力对比及提升计划

## 执行能力对比

### 核心执行模型

| 特性 | Engineering Flow Platform | CodeW | 差距 | 提升优先级 |
|------|----------|-------|------|-----------|
| **执行队列** | Per-session + Global 队列序列化 | 无队列 | 🔴 高 | P0 |
| **生命周期事件** | start/end/error 完整事件流 | 无 | 🔴 高 | P0 |
| **超时控制** | 可配置 timeoutSeconds (默认600s) | 硬编码 120s | 🟡 中 | P1 |
| **流式响应** | Tool streaming + Block streaming | 无 | 🔴 高 | P0 |
| **重试策略** | 智能重试 + Compaction 重试 | 简单指数退避 | 🟡 中 | P1 |
| **Hook 系统** | 6种 hook 拦截点 | 无 | 🔴 高 | P2 |

### 会话管理

| 特性 | Engineering Flow Platform | CodeW | 差距 | 提升优先级 |
|------|----------|-------|------|-----------|
| **会话持久化** | JSONL 转录 + Store 文件 | 仅内存 | 🔴 高 | P0 |
| **Token 追踪** | input/output/total/context 全量 | 无 | 🔴 高 | P0 |
| **会话剪裁** | Auto-pruning 旧工具结果 | 无 | 🔴 高 | P1 |
| **会话压缩** | Auto-compaction + 手动 `/compact` | 无 | 🔴 高 | P1 |
| **内存flush** | 预压缩内存刷新 | 无 | 🟡 中 | P2 |
| **重置策略** | Daily/Idle/Per-type/Per-channel | 无 | 🟡 中 | P1 |
| **DM 作用域** | main/per-peer/per-channel-peer | 单级 | 🟡 中 | P2 |
| **身份链接** | 跨渠道身份映射 | 无 | 🟡 中 | P2 |
| **发送策略** | 基于规则的发送控制 | 无 | 🟢 低 | P3 |

### 缓存机制

| 特性 | Engineering Flow Platform | CodeW | 差距 | 提升优先级 |
|------|----------|-------|------|-----------|
| **技能快照** | Skills 加载后快照复用 | 每次重新加载 | 🟡 中 | P1 |
| **配置缓存** | 热重载 + 缓存 | 仅热重载 | 🟢 低 | P2 |
| **模型/认证** | Auth profile 轮询 + 降级 | 无 | 🔴 高 | P2 |
| **工具结果** | 剪裁但保留必要历史 | 仅保留 N 条 | 🟡 中 | P1 |

---

## 提升计划 (按优先级)

### P0 - 核心基础设施

#### 1. 会话持久化 (session/persistence.py)
```python
# 实现目标
- JSONL 转录文件存储
- sessions.json Store 管理
- 启动时加载历史会话
```

**文件结构**:
```
sessions/
├── sessions.json          # Store: sessionKey -> metadata
├── main/
│   └── <sessionId>.jsonl  # 转录文件
└── jira:<issueKey>/
    └── <sessionId>.jsonl
```

**实现功能**:
- `SessionStore` 类管理 JSONL 文件
- 自动创建会话目录
- 追加式写入转录
- 按会话 ID 查询历史
- 清理过期会话

#### 2. Token 追踪 (session/usage.py)
```python
# 实现目标
- 追踪每次 LLM 调用的 token 消耗
- 统计会话总使用量
- 支持 usage 报告
```

**数据结构**:
```python
class UsageStats:
    input_tokens: int
    output_tokens: int
    total_tokens: int
    context_tokens: int
    model: str
    cost: Optional[float]  # 如果有定价
    timestamp: str
```

#### 3. 基础执行队列 (agent/queue.py)
```python
# 实现目标
- Per-session 序列化队列
- 避免并发执行冲突
```

**功能**:
```python
class ExecutionQueue:
    async def enqueue(session_id: str, coro: Coroutine) -> Any:
        """将任务加入会话队列，序列化执行"""
        
    async def get_queue_status(session_id: str) -> dict:
        """获取队列状态"""
```

---

### P1 - 重要功能

#### 4. 流式响应支持 (llm/stream.py)
```python
# 实现目标
- 支持 LLM 流式响应
- 实现 Tool streaming 事件
```

**API 设计**:
```python
class StreamClient:
    async def chat_stream(
        messages: List[Dict],
        system_prompt: str,
        tools: Optional[List] = None,
    ) -> AsyncIterator[Delta]:
        """流式响应迭代器"""
        
    async def tool_event_stream(
        tool_name: str,
        status: str,  # start/update/end
        data: Any
    ) -> AsyncIterator[ToolEvent]:
        """工具事件流"""
```

#### 5. 会话剪裁 (session/pruning.py)
```python
# 实现目标
- 自动剪裁旧工具结果
- 保留关键上下文
```

**策略**:
```python
class SessionPruner:
    async def prune(session: Session) -> Session:
        """剪裁会话，移除过时的工具结果"""
        # 1. 保留系统提示
        # 2. 保留最近 N 轮对话
        # 3. 剪裁旧工具调用细节，保留摘要
        # 4. 压缩长文本
```

#### 6. 会话压缩 (session/compaction.py)
```python
# 实现目标
- 实现 `/compact` 命令
- 自动压缩触发
```

**流程**:
```
用户发送 /compact [可选说明]
    ↓
生成压缩摘要提示
    ↓
调用 LLM 生成摘要
    ↓
替换旧历史为摘要
    ↓
记录压缩事件
```

---

### P2 - 增强功能

#### 7. 重置策略 (session/reset.py)
```python
# 实现目标
- Daily reset (默认 4AM)
- Idle reset (可配置分钟数)
- Per-type/Per-channel 覆盖
```

#### 8. 技能快照缓存 (skills/snapshot.py)
```python
# 实现目标
- Skills 加载后缓存
- 避免重复文件读取
```

#### 9. Hook 系统 (agent/hooks.py)
```python
# 实现目标
- 基础 Hook 接口
- 支持 before_agent_start, agent_end
```

---

### P3 - 高级功能

#### 10. 多 DM 作用域
```python
class SessionScope:
    MAIN = "main"                    # 所有 DMs 共享
    PER_PEER = "per-peer"           # 按发送者隔离
    PER_CHANNEL_PEER = "per-channel-peer"  # 按渠道+发送者
    PER_ACCOUNT_CHANNEL_PEER = "per-account-channel-peer"  # 完整隔离
```

#### 11. 身份链接
```python
class IdentityLinker:
    def link(canonical_id: str, provider_ids: List[str]):
        """映射跨渠道身份"""
        
    def resolve(provider: str, peer_id: str) -> str:
        """解析规范身份"""
```

---

## 实施顺序

### Phase 1: 核心持久化 (本周)
1. ✅ `session/persistence.py` - JSONL + Store
2. ✅ `session/usage.py` - Token 追踪
3. ✅ `session/reset.py` - 重置策略

### Phase 2: 执行增强 (下周)
1. ✅ `agent/queue.py` - 执行队列
2. ✅ `llm/stream.py` - 流式响应
3. ✅ `session/pruning.py` - 会话剪裁

### Phase 3: 高级特性 (下月)
1. ✅ `session/compaction.py` - 会话压缩
2. ✅ `skills/snapshot.py` - 技能缓存
3. ✅ `agent/hooks.py` - Hook 系统

---

## 当前代码 vs 目标代码对比

### 当前 LLMClient (llm.py)
```python
class LLMClient:
    async def chat(self, messages, system_prompt=None, tools=None) -> Dict:
        # 简单请求-响应
        # 无流式
        # 无中间事件
```

### 目标 LLMClient (llm.py)
```python
class LLMClient:
    async def chat(self, messages, system_prompt=None, tools=None) -> Dict:
        # 记录 token 使用
        # 更新 usage 统计
    
    async def chat_stream(self, messages, system_prompt=None, tools=None) -> AsyncIterator:
        # 流式响应
        # 触发 tool_start/update/end 事件
    
    def record_usage(self, response: Dict, model: str):
        # 提取 token 计数
        # 写入 usage.jsonl
```

### 当前 SessionManager (manager.py)
```python
class SessionManager:
    def __init__(self, max_history: int = 5):
        self.sessions: Dict[str, Dict] = {}
        # 仅内存存储
```

### 目标 SessionManager (manager.py)
```python
class SessionManager:
    def __init__(self, max_history: int = 5):
        self.store = SessionStore()  # 持久化
        self.usage = UsageTracker()  # Token 追踪
        self.pruner = SessionPruner()  # 剪裁
        self.reset_policy = ResetPolicy()  # 重置策略
```

---

## 测试计划

### 单元测试
```python
# test_persistence.py
def test_session_store_crud():
    # 创建/读取/更新/删除会话
    
def test_jsonl_append_and_read():
    # 追加转录
    # 读取历史
    
# test_usage.py
def test_usage_tracking():
    # 记录 token 使用
    # 统计汇总
    
# test_pruning.py
def test_session_pruning():
    # 剪裁旧结果
    # 保留摘要
```

### 集成测试
```python
def test_full_session_lifecycle():
    # 1. 创建会话
    # 2. 多次对话
    # 3. 检查 token 统计
    # 4. 触发重置
    # 5. 验证持久化
```

---

## 风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| 磁盘 I/O 性能 | 高频写入影响响应 | 批量写入 + 异步 |
| 存储膨胀 | 会话历史无限增长 | 压缩策略 + 清理 |
| 向后兼容 | 破坏现有会话 | 迁移脚本 + 版本控制 |

---

*最后更新: 2026-02-02*
*文档版本: v1.0*
