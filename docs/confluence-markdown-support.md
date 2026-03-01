# Confluence Markdown 支持 - 需求文档

## 1. 背景

当前 Confluence 集成使用 Atlassian 的 Storage Format (XML-based HTML) 作为内容格式。这对于开发者不够友好，尤其是：

- 用户更习惯 Markdown 语法
- Markdown 更易读、易写
- 与 GitHub/Jira 等工具格式统一

## 2. 目标

为 Confluence 集成增加 Markdown 格式支持，使其成为**默认格式**，同时保留现有的 Storage Format 作为备选。

## 3. 功能需求

### 3.1 查询 - Markdown 默认

| 场景 | 当前行为 | 期望行为 |
|------|----------|----------|
| `confluence_get_page` | 返回 HTML storage format | 返回 Markdown (默认) |
| `confluence_get_page_by_url` | 返回 HTML storage format | 返回 Markdown (默认) |
| `confluence_search` | 返回 HTML snippets | 返回 Markdown snippets |
| `confluence_list_pages` | 返回 HTML | 返回 Markdown 标题 |

**新增参数:**
```python
# 所有获取页面的函数新增参数
format: "markdown" | "storage"  # 默认 "markdown"
```

### 3.2 创建 - Markdown 输入

| 场景 | 当前行为 | 期望行为 |
|------|----------|----------|
| `confluence_create_page` | 需要 HTML 输入 | 接受 Markdown (自动转换) |

**新增参数:**
```python
confluence_create_page(
    space_key: str,
    title: str,
    body: str,           # 现在接受 Markdown
    body_format: "markdown" | "storage",  # 默认 "markdown"
    parent_id: str = None
)
```

### 3.3 更新 - Markdown 输入

| 场景 | 当前行为 | 期望行为 |
|------|----------|----------|
| `confluence_update_page` | 需要 HTML 输入 | 接受 Markdown (自动转换) |

**新增参数:**
```python
confluence_update_page(
    page_id: str,
    title: str = None,
    body: str = None,
    body_format: "markdown" | "storage"  # 默认 "markdown"
)
```

### 3.4 保留现有 Storage 方式

- 所有函数保留 `body_format` / `format` 参数
- 显式指定 `body_format: "storage"` 时使用原有行为
- 不指定时默认 Markdown

## 4. 技术方案

### 4.1 Markdown → Storage 转换

使用 `markdown2confluence` 库或自实现转换器:

```python
# 推荐库: markdown2confluence
# 安装: pip install markdown2confluence

import markdown2confluence

def markdown_to_storage(markdown_text: str) -> str:
    """Convert Markdown to Confluence Storage Format"""
    return markdown2confluence.convert(markdown_text)
```

### 4.2 Storage → Markdown 转换

使用 `confluence-markdown` 或自实现:

```python
# 推荐方案: 使用正则 + BeautifulSoup 解析
# 或 confluence-markdown 库

def storage_to_markdown(storage_text: str) -> str:
    """Convert Confluence Storage Format to Markdown"""
    # 实现见技术设计文档
```

### 4.3 转换映射表

| Markdown | Confluence Storage |
|----------|-------------------|
| `# Title` | `<h1>Title</h1>` |
| `**bold**` | `<strong>bold</strong>` |
| `*italic*` | `<em>italic</em>` |
| `~~strike~~` | `<strike>strike</strike>` |
| ``code`` | `<code>code</code>` |
| ```lang<br>code``` | `<ac:code-block lang="lang">code</ac:code-block>` |
| `[link](url)` | `<a href="url">link</a>` |
| `![alt](url)` | `<ac:image><ri:url ri:value="url"/></ac:image>` |
| `- item` | `<ul><li>item</li></ul>` |
| `1. item` | `<ol><li>item</li></ol>` |
| `> quote` | `<blockquote>quote</blockquote>` |
| `---` | `<hr/>` |
| `\| col1 \| col2 \|` | `<table><tr><td>col1</td><td>col2</td></tr></table>` |

### 4.4 目录结构

```
src/confluence/
├── api.py              # ConfluenceChannel (现有)
├── __init__.py         # 工具函数 (现有)
├── converter.py        # 新增: Markdown ↔ Storage 转换器
└── markdown.py         # 新增: Markdown 格式工具函数
```

## 5. API 变更

### 5.1 新增函数

```python
# src/confluence/markdown.py
async def confluence_get_page_markdown(page_id: str) -> str:
    """Get page content as Markdown"""

async def confluence_create_page_markdown(
    space_key: str,
    title: str,
    body: str,  # Markdown
    parent_id: str = None
) -> str:
    """Create page with Markdown content"""

async def confluence_update_page_markdown(
    page_id: str,
    title: str = None,
    body: str = None  # Markdown
) -> str:
    """Update page with Markdown content"""
```

### 5.2 现有函数参数变更

```python
# confluence_get_page 新增参数
async def confluence_get_page(
    page_id: str,
    format: str = "markdown"  # 新增，默认 markdown
) -> str

# confluence_create_page 新增参数
async def confluence_create_page(
    space_key: str,
    title: str,
    body: str = "",
    body_format: str = "markdown",  # 新增，默认 markdown
    parent_id: str = None
) -> str
```

## 6. Tool Schema 变更

```json
{
  "name": "confluence_get_page",
  "description": "Get a Confluence page by its ID. Returns Markdown by default.",
  "parameters": {
    "type": "object",
    "properties": {
      "page_id": {"type": "string"},
      "format": {"type": "string", "enum": ["markdown", "storage"], "default": "markdown"}
    },
    "required": ["page_id"]
  }
}
```

## 7. 测试计划

### 7.1 单元测试

- `test_markdown_to_storage_basic` - 基本转换
- `test_markdown_to_storage_complex` - 复杂格式
- `test_storage_to_markdown_basic` - 反向转换
- `test_storage_to_markdown_preserves_formatting` - 格式保留

### 7.2 集成测试

- `test_create_page_with_markdown` - Markdown 创建
- `test_update_page_with_markdown` - Markdown 更新
- `test_get_page_returns_markdown` - Markdown 查询
- `test_backward_compatibility_storage` - 兼容原有 Storage

### 7.3 边界测试

- 空内容
- 超大文档 (>100KB)
- 特殊字符
- 嵌套结构

## 8. 配置项

```yaml
confluence:
  enabled: true
  default_format: "markdown"  # 新增: 默认格式
  instances:
    - name: "Default"
      # ... existing config
```

## 9. 里程碑

- [ ] M1: 实现 Markdown ↔ Storage 转换器
- [ ] M2: 更新 `confluence_get_page` 支持 format 参数
- [ ] M3: 更新 `confluence_create_page` 支持 body_format 参数
- [ ] M4: 更新 `confluence_update_page` 支持 body_format 参数
- [ ] M5: 更新 Tool Schema
- [ ] M6: 单元测试 + 集成测试
- [ ] M7: 文档更新

## 10. 依赖

```txt
# requirements.txt 新增
markdown2confluence>=1.0.0
```

## 11. 风险与限制

1. **转换丢失**: 某些 Confluence 特有元素可能无法完美转换 (如 macros)
2. **性能**: 大文档转换可能有性能开销
3. **版本兼容**: 依赖库版本稳定性

## 12. 替代方案

如果 `markdown2confluence` 库不符合需求，可以:
- 使用正则表达式自实现 (推荐用于核心功能)
- 使用 `pandoc` 命令行转换
