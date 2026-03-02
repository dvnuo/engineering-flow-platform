# Jira Markdown 支持 - 需求文档

## 1. 背景

当前 Jira 集成使用 Atlassian 的 REST API 返回的原始格式（通常是 JSON 或 Atlassian Document Format）。为了与 Confluence 保持一致，并提升开发者体验，需要为 Jira 增加 Markdown 格式支持。

## 2. 目标

为 Jira 集成增加 Markdown 格式支持：
- 查询（Issue 内容）默认返回 Markdown
- 创建/更新接受 Markdown 输入
- 保留现有格式作为备选

## 3. 功能需求

### 3.1 查询 - Markdown 默认

| 场景 | 当前行为 | 期望行为 |
|------|----------|----------|
| `jira_get_issue` | 返回 JSON / ADF | 返回 Markdown (默认) |
| `jira_get_comments` | 返回 JSON | 返回 Markdown (默认) |

**新增参数:**
```python
jira_get_issue(
    issue_key: str,
    format: "markdown" | "raw",  # 默认 "markdown"
    max_chars: int = None  # 可选截断
)
```

### 3.2 创建/更新 - Markdown 输入

| 场景 | 当前行为 | 期望行为 |
|------|----------|----------|
| `jira_create_issue` | 需要 JSON/ADF | 接受 Markdown (自动转换) |
| `jira_update_issue` | 需要 JSON/ADF | 接受 Markdown (自动转换) |
| `jira_add_comment` | 需要 JSON | 接受 Markdown (自动转换) |

**新增参数:**
```python
jira_create_issue(
    project_key: str,
    summary: str,
    description: str = "",  # Markdown
    description_format: "markdown" | "raw",  # 默认 "markdown"
    issue_type: str = "Bug",
    priority: str = None,
    ...
)

jira_add_comment(
    issue_key: str,
    body: str,  # Markdown
    body_format: "markdown" | "raw",  # 默认 "markdown"
)
```

### 3.3 保留现有方式

- 所有函数保留 `format` / `body_format` 参数
- 显式指定 `format: "raw"` 时使用原有行为
- 不指定时默认 Markdown

## 4. 技术方案

### 4.1 目录结构

```
src/jira/
├── api.py              # JiraChannel (现有)
├── __init__.py         # 工具函数 (现有)
├── converter.py        # 新增: Markdown ↔ Jira ADF 转换器
└── adapter.py         # 新增: 格式适配器
```

### 4.2 Jira ADF (Atlassian Document Format) 转换

Jira 使用 ADF 格式，需要实现：
- ADF → Markdown
- Markdown → ADF

**ADF 元素映射:**

| ADF | Markdown |
|-----|----------|
| `heading` | `#` |
| `paragraph` | 文本 |
| `bulletList` | `-` |
| `orderedList` | `1.` |
| `codeBlock` | ```lang |
| `codeMark` | ``code`` |
| `strong` | **bold** |
| `emphasis` | *italic* |
| `link` | [text](url) |
| `media` | ![](url) |
| `blockquote` | `>` |

### 4.3 Format Adapter 设计

```python
# src/jira/adapter.py
class JiraFormatAdapter:
    def __init__(self, channel: JiraChannel):
        self.channel = channel
        self.converter = JiraMarkdownConverter()
    
    async def get_issue(self, issue_key: str, format: str = "markdown", max_chars: int = None) -> str:
        issue = await self.channel.get_issue(issue_key)
        
        if format == "raw":
            return self._extract_raw(issue)
        
        # format == "markdown"
        return self._to_markdown(issue, max_chars)
    
    async def add_comment(self, issue_key: str, body: str, body_format: str = "markdown") -> str:
        if body_format == "markdown":
            body = self.converter.markdown_to_adf(body)
        
        return await self.channel.add_comment(issue_key, body)
```

## 5. API 变更

### 5.1 新增参数

```python
# jira_get_issue
async def jira_get_issue(
    issue_key: str,
    format: str = "markdown",
    max_chars: int = None
) -> str

# jira_create_issue  
async def jira_create_issue(
    project_key: str,
    summary: str,
    description: str = "",
    description_format: str = "markdown",
    ...
) -> str

# jira_add_comment
async def jira_add_comment(
    issue_key: str,
    body: str,
    body_format: str = "markdown"
) -> str
```

## 6. Tool Schema 变更

```json
{
  "name": "jira_get_issue",
  "description": "Get a Jira issue by key. Returns Markdown by default.",
  "parameters": {
    "type": "object",
    "properties": {
      "issue_key": {"type": "string", "description": "Jira issue key (e.g., 'PROJ-123')"},
      "format": {
        "type": "string",
        "enum": ["markdown", "raw"],
        "default": "markdown",
        "description": "Output format: markdown (default) or raw"
      },
      "max_chars": {
        "type": "integer",
        "description": "Maximum characters to return"
      }
    },
    "required": ["issue_key"]
  }
}
```

## 7. 已知限制

1. **ADF 复杂性**: Jira ADF 是复杂的 JSON 结构，完整转换可能有遗漏
2. **宏/组件**: 某些 Jira 特有元素可能无法完美转换
3. **附件**: 附件处理需要单独实现

## 8. 里程碑

- [ ] M1: 实现 JiraMarkdownConverter (ADF ↔ Markdown)
- [ ] M2: 创建 JiraFormatAdapter
- [ ] M3: 更新 jira_get_issue 支持 format 参数
- [ ] M4: 更新 jira_create_issue 支持 description_format
- [ ] M5: 更新 jira_add_comment 支持 body_format
- [ ] M6: 更新 Tool Schema
- [ ] M7: 单元测试 + 集成测试

## 9. 参考

- Confluence Markdown 支持实现: `src/confluence/converter.py`, `src/confluence/adapter.py`
- Jira REST API: https://developer.atlassian.com/server/jira/platform/rest-apis/
- Atlassian Document Format: https://developer.atlassian.com/cloud/jira/platform/apis/document/structure/
