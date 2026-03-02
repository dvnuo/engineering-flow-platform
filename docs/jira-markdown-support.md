# Jira Markdown 支持 - 需求文档

## 1. 背景

当前 Jira 集成使用 Atlassian 的 REST API 返回的原始格式。

**Jira 私有化 (Server/Data Center):**
- `description` / `comment.body` 通常是 **wiki/renderer 字符串**（REST v2）
- 这是私有化环境最常见的格式

**Jira Cloud:**
- 使用 **ADF (Atlassian Document Format)** - JSON 结构（REST v3）

**本需求设计:**
- 以 **Server/DC** 为主场景
- **主路径**: wiki/renderer ↔ Markdown
- **兼容路径**: ADF (Cloud) ↔ Markdown（检测到 ADF JSON 时启用）

## 2. 目标

为 Jira 集成增加 Markdown 格式支持：
- 查询（Issue 内容）默认返回 Markdown
- 创建/更新/评论接受 Markdown 输入
- 保留 wiki 和 raw 作为备选

## 3. 功能需求

### 3.1 查询 - Markdown 默认

| 场景 | 当前行为 | 期望行为 |
|------|----------|----------|
| `jira_get_issue` | 返回 JSON | 返回 Markdown (默认) |
| `jira_get_comments` | 返回 JSON | 返回 Markdown (默认) |

**新增参数:**
```python
jira_get_issue(
    issue_key: str,
    format: "markdown" | "wiki" | "raw",  # 默认 "markdown"
    max_chars: int = None,
    max_comments: int = 5,
    include_fields: list[str] = None,  # ["summary", "status", "description"]
)
```

### 3.2 创建/更新 - Markdown 输入

| 场景 | 当前行为 | 期望行为 |
|------|----------|----------|
| `jira_create_issue` | 需要 JSON | 接受 Markdown (自动转换) |
| `jira_update_issue` | 需要 JSON | 接受 Markdown (自动转换) |
| `jira_add_comment` | 需要 JSON | 接受 Markdown (自动转换) |

**新增参数:**
```python
jira_create_issue(
    project_key: str,
    summary: str,
    description: str = "",  # Markdown
    description_format: "markdown" | "wiki" | "raw",  # 默认 "markdown"
    issue_type: str = "Bug",
    ...
)

jira_add_comment(
    issue_key: str,
    body: str,  # Markdown
    body_format: "markdown" | "wiki" | "raw",  # 默认 "markdown"
)
```

### 3.3 format 参数说明

| 值 | 说明 | 用途 |
|-----|------|------|
| `markdown` | LLM 友好的 Markdown | **默认**，给 AI 用 |
| `wiki` | Jira 私有化可渲染的源文本 | 写回/渲染用 |
| `raw` | 完整 JSON / issue 原始数据 | 调试/兼容用 |

### 3.4 保留现有方式

- 所有函数保留 `format` / `body_format` 参数
- 显式指定 `format: "wiki"` 或 `format: "raw"` 时使用对应格式
- 不指定时默认 **Markdown**

## 4. 技术方案

### 4.1 目录结构

```
src/jira/
├── api.py              # JiraChannel (现有)
├── __init__.py         # 工具函数 (现有)
├── converter.py        # Markdown ↔ Jira wiki (ADF 兼容)
└── adapter.py          # 格式适配器
```

### 4.2 主路径：Jira wiki/renderer ↔ Markdown (Server/DC)

**核心映射 (最小支持):**

| Markdown | Jira Wiki |
|----------|-----------|
| `# Title` | h1. Title |
| `## Title` | h2. Title |
| `### Title` | h3. Title |
| `**bold**` | *bold* |
| `*italic*` | _italic_ |
| `` `code` `` | `{{code}}` |
| ` ```lang<br>code``` ` | `{code:lang}<br>code<br>{code}` |
| `[link](url)` | [link\|url] |
| `- item` | * item |
| `1. item` | # item |
| `> quote` | {quote}quote{/quote} |
| `---` | ---- |
| `![alt](url)` | !url! |

**说明:**
- inline code: 使用 `{{code}}`
- block code: 使用 `{code:lang}...{code}`

**实现策略:**
- 不是追求 100% round-trip
- 优先保证 "LLM 可读、token 省"
- wiki → markdown：解析 wiki 标记
- markdown → wiki：生成 wiki 标记

### 4.3 兼容路径：ADF ↔ Markdown (Cloud)

**仅当检测到 ADF JSON 时启用**（body 是 dict 且包含 `type: doc` 等特征）。

**ADF 元素映射:**

| ADF | Markdown |
|-----|----------|
| `heading` | # |
| `paragraph` | 文本 |
| `bulletList` | - |
| `orderedList` | 1. |
| `codeBlock` | ```lang |
| `codeMark` | ``code`` |
| `strong` | **bold** |
| `emphasis` | *italic* |
| `link` | [text](url) |

### 4.4 Format Adapter 设计

```python
# src/jira/adapter.py
class JiraFormatAdapter:
    def __init__(self, channel: JiraChannel):
        self.channel = channel
        self.converter = JiraMarkupConverter()
        # 部署类型：Server/DC 或 Cloud（从 channel 配置获取）
        self.deployment = getattr(channel, 'deployment', 'server')
    
    async def get_issue(
        self,
        issue_key: str,
        format: str = "markdown",
        max_chars: int = None,
        max_comments: int = 5,
        include_fields: list[str] = None,
        include_comments: bool = True
    ) -> str:
        issue = await self.channel.get_issue(issue_key)
        
        if format == "raw":
            # raw 返回原始 dict（用于调试/兼容）
            return self._format_raw(issue, include_fields, include_comments)
        
        if format == "wiki":
            return self._to_wiki(issue, max_chars, max_comments, include_fields, include_comments)
        
        # format == "markdown" (default)
        return self._to_markdown(issue, max_chars, max_comments, include_fields, include_comments)
    
    def _to_markdown(
        self,
        issue: dict,
        max_chars: int = None,
        max_comments: int = 5,
        include_fields: list[str] = None,
        include_comments: bool = True
    ) -> str:
        """转换 issue 为 Markdown 格式"""
        # 构建 Markdown
        lines = []
        
        fields = include_fields or ["summary", "status", "description"]
        
        if "summary" in fields:
            lines.append(f"# {issue.get('fields', {}).get('summary', '')}")
        
        if "status" in fields:
            status = issue.get('fields', {}).get('status', {})
            lines.append(f"**Status:** {status.get('name', '')}")
        
        if "description" in fields:
            desc = issue.get('fields', {}).get('description')
            if desc:
                # 检测是 wiki 还是 ADF
                if isinstance(desc, dict):
                    # ADF - 转换为 Markdown
                    md = self.converter.adf_to_markdown(desc)
                else:
                    # wiki 字符串
                    md = self.converter.wiki_to_markdown(str(desc))
                lines.append(f"\n{md}")
        
        if include_comments and "comments" in fields:
            lines.append("\n## Comments")
            # 添加评论...
        
        result = "\n".join(lines)
        
        # 截断处理
        if max_chars:
            result = self._truncate_markdown(result, max_chars)
        
        return result
    
    def _truncate_markdown(self, text: str, max_chars: int) -> str:
        """安全截断 Markdown，优先按段落边界截断"""
        # 优先按段落边界 (\n\n) 截断
        paragraphs = text.split('\n\n')
        result = []
        total = 0
        
        for p in paragraphs:
            if total + len(p) + 2 <= max_chars:
                result.append(p)
                total += len(p) + 2
            else:
                # 剩余空间不够，按句子截断
                remaining = max_chars - total
                if remaining > 50:  # 至少保留一些内容
                    result.append(p[:remaining])
                break
        
        truncated = "\n\n".join(result)
        if len(text) > max_chars:
            truncated += f"\n\n... (truncated, {max_chars} chars limit)"
        
        return truncated
    
    async def add_comment(
        self,
        issue_key: str,
        body: str,
        body_format: str = "markdown"
    ) -> str:
        """添加评论"""
        # 根据部署类型选择转换路径
        # Server/DC: markdown -> wiki
        # Cloud: markdown -> ADF
        if body_format == "markdown":
            if self.deployment == "cloud":
                body = self.converter.markdown_to_adf(body)
            else:
                # Server/DC: 默认转 wiki
                body = self.converter.markdown_to_wiki(body)
        # body_format == "wiki" 或 "raw" 时不做转换
        
        return await self.channel.add_comment(issue_key, body)
```

## 5. API 变更

### 5.1 新增参数

```python
from typing import Union

# jira_get_issue
async def jira_get_issue(
    issue_key: str,
    format: str = "markdown",  # markdown | wiki | raw
    max_chars: int = None,
    max_comments: int = 5,
    include_fields: list[str] = None,  # 默认: ["summary", "status", "description", "comments"]
    include_comments: bool = True  # 是否包含评论
) -> Union[str, dict]:
    """获取 Jira Issue
    
    Returns:
        markdown/wiki: str
        raw: dict (完整 issue JSON)
    """

# jira_create_issue
async def jira_create_issue(
    project_key: str,
    summary: str,
    description: str = "",
    description_format: str = "markdown",  # markdown | wiki | raw
    issue_type: str = "Bug",
    ...
) -> str

# jira_add_comment
async def jira_add_comment(
    issue_key: str,
    body: str,
    body_format: str = "markdown"  # markdown | wiki | raw
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
        "enum": ["markdown", "wiki", "raw"],
        "default": "markdown",
        "description": "Output format: markdown (LLM-friendly), wiki (renderable), or raw (JSON)"
      },
      "max_chars": {
        "type": "integer",
        "description": "Maximum characters to return"
      },
      "max_comments": {
        "type": "integer",
        "description": "Maximum number of comments to include",
        "default": 5
      },
      "include_fields": {
        "type": "array",
        "items": {"type": "string"},
        "description": "Fields to include (default: summary, status, description, comments)"
      },
      "include_comments": {
        "type": "boolean",
        "description": "Whether to include comments",
        "default": true
      }
    },
    "required": ["issue_key"]
  }
}
```

## 7. 已知限制

### 7.1 Wiki/Renderer 方言差异
- 不同 Jira 版本/渲染器对语法支持不同
- 转换以常用语法为主

### 7.2 往返不保证一致
- Markdown → wiki → Markdown 可能丢失部分格式

### 7.3 ADF 仅兼容
- 如遇 ADF JSON，尽力转换
- 不保证覆盖全部节点

## 8. 里程碑

- [ ] M1: 实现 JiraMarkupConverter (wiki ↔ markdown + ADF 兼容分支)
- [ ] M2: 创建 JiraFormatAdapter
- [ ] M3: 更新 jira_get_issue 支持 format/max_chars/max_comments/include_fields/include_comments
- [ ] M4: 更新 jira_create_issue 支持 description_format
- [ ] M5: 更新 jira_add_comment 支持 body_format
- [ ] M6: 更新 Tool Schema
- [ ] M7: 单元测试 + 集成测试

## 9. 对外契约（一句话版）

**读:** 默认返回 Markdown（LLM-friendly），可选返回 wiki 或 raw JSON

**写:** 默认接受 Markdown，内部转换成 Jira 可写入的 wiki（Server/DC），ADF 仅作兼容

## 10. 参考

- Confluence Markdown 支持实现: `src/confluence/converter.py`, `src/confluence/adapter.py`
- Jira REST API: https://developer.atlassian.com/server/jira/platform/rest-apis/
- Jira wiki markup: https://confluence.atlassian.com/doc/wiki-markup-228382720.html
- Atlassian Document Format: https://developer.atlassian.com/cloud/jira/platform/apis/document/structure/
