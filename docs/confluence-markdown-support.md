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
| `confluence_search` | 返回 HTML snippets | 返回 title + url + excerpt (文本) |
| `confluence_list_pages` | 返回 HTML | 返回 Markdown 标题 |

**新增参数:**
```python
# 所有获取页面的函数新增参数
format: "markdown" | "storage"  # 默认 "markdown"
max_chars: int = None  # 返回最多 N 字符 (避免 token 爆)
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
    body: str,                     # 现在接受 Markdown
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

### 4.1 转换器实现

当前使用**内置正则转换器**，支持常见 Markdown 元素:

| 元素 | 支持 |
|------|------|
| Headers (h1-h6) | ✅ |
| Bold/Italic/Strike | ✅ |
| Code blocks / inline | ✅ |
| Links / Images | ✅ |
| Lists (ul/ol) | ✅ |
| Tables | ✅ |
| Horizontal rules | ✅ |

> 注: 当前使用内置正则转换器，不需要外部依赖。

### 4.2 目录结构

```
src/confluence/
├── api.py              # ConfluenceChannel (现有)
├── __init__.py         # 工具函数 (现有)
├── adapter.py          # 新增: Format Adapter (统一入口)
└── converter.py        # 新增: 转换器封装
```

### 4.3 Format Adapter 设计

```python
# src/confluence/adapter.py
class ConfluenceFormatAdapter:
    """统一处理 Markdown/Storage 格式转换"""
    
    def __init__(self, channel: ConfluenceChannel):
        self.channel = channel
    
    # ========== 读 ==========
    async def get_page(self, page_id: str, format: str = "markdown", max_chars: int = None) -> str:
        """
        获取页面内容
        
        Args:
            page_id: 页面 ID
            format: "markdown" | "storage"
            max_chars: 最多返回字符数
        """
        page = await self.channel.get_page(page_id)
        
        if format == "storage":
            return self._extract_storage(page)
        
        # format == "markdown": 使用内置 converter
        return self._to_markdown(page, max_chars)
    
    async def search(self, query: str, limit: int = 10) -> str:
        """搜索 - 返回 title + url + excerpt"""
        result = await self.channel.search_pages(query, limit)
        return self._format_search_results(result)
    
    # ========== 写 ==========
    async def create_page(
        self,
        space_key: str,
        title: str,
        body: str,
        body_format: str = "markdown",
        parent_id: str = None
    ) -> str:
        """创建页面"""
        if body_format == "markdown":
            body = self._to_storage(body)
        
        return await self.channel.create_page(space_key, title, body, parent_id)
    
    async def update_page(
        self,
        page_id: str,
        title: str = None,
        body: str = None,
        body_format: str = "markdown"
    ) -> str:
        """更新页面"""
        if body and body_format == "markdown":
            body = self.converter.markdown_to_storage(body)
        
        return await self.channel.update_page(page_id, title, body)
    
    # ========== 内部方法 ==========
    def _to_markdown(self, page: dict, max_chars: int = None) -> str:
        """使用内置 converter 转换为 Markdown"""
        title = page.get("title", "Untitled")
        body_obj = page.get("body", {})
        if isinstance(body_obj, dict):
            storage_value = body_obj.get("storage", {}).get("value", "")
        else:
            storage_value = ""
        
        if not storage_value:
            return f"# {title}\n\n_No content_"
        
        return f"# {title}\n\n{self.converter.storage_to_markdown(storage_value)}"
    
    def _to_storage(self, markdown: str) -> str:
        """使用内置 converter 转换为 Storage Format"""
        return self.converter.markdown_to_storage(markdown)
```

### 4.4 转换映射表

| Markdown | Confluence Storage |
|----------|-------------------|
| `# Title` | `<h1>Title</h1>` |
| `**bold**` | `<strong>bold</strong>` |
| `*italic*` | `<em>italic</em>` |
| ``code`` | `<code>code</code>` |
| ```lang<br>code``` | `<ac:code-block lang="lang">code</ac:code-block>` |
| `[link](url)` | `<a href="url">link</a>` |
| `![alt](url)` | `<ac:image><ri:url ri:value="url"/></ac:image>` |
| `- item` | `<ul><li>item</li></ul>` |
| `> quote` | `<blockquote>quote</blockquote>` |

## 5. API 变更

### 5.1 现有函数参数变更

```python
# confluence_get_page 新增参数
async def confluence_get_page(
    page_id: str,
    format: str = "markdown",    # 新增，默认 markdown
    max_chars: int = None        # 新增，限制返回长度
) -> str

# confluence_create_page 新增参数
async def confluence_create_page(
    space_key: str,
    title: str,
    body: str = "",
    body_format: str = "markdown",  # 新增，默认 markdown
    parent_id: str = None
) -> str

# confluence_update_page 新增参数
async def confluence_update_page(
    page_id: str,
    title: str = None,
    body: str = None,
    body_format: str = "markdown"  # 新增，默认 markdown
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
      "page_id": {"type": "string", "description": "Confluence page ID"},
      "format": {
        "type": "string",
        "enum": ["markdown", "storage"],
        "default": "markdown",
        "description": "Output format: markdown (default) or storage"
      },
      "max_chars": {
        "type": "integer",
        "description": "Maximum characters to return (avoid token overflow)",
        "default": null
      }
    },
    "required": ["page_id"]
  }
}
```

## 7. 已知风险与限制 ⚠️

### 坑 1: Markdown 方言不一致

- 内置正则转换器 导出时尽量保留元素
- 某些元素可能往返不一致
- **Confluence 特有元素** (macro, panel, status, expand, layouts) 可能往返不一致

**建议:** 在 converter.py 定义"支持矩阵"，列出"不保证往返一致"的元素，并在日志打 warning。

### 坑 2: search 返回的是 snippet 而非完整 body

- Confluence search API 的 snippet 是简化 HTML 片段
- 不是完整的 body.storage

**建议:** search 结果只返回 title + url + excerpt (纯文本)，用户需要正文时再调用 get_page。

### 坑 3: update 需要处理 version 冲突

- Confluence 更新页面必须带 version 递增
- 否则返回 409 冲突

**建议:** 在集成测试中验证 API 是否正确处理 version。

## 8. 测试计划

### 8.1 单元测试

- [ ] `test_markdown_to_storage_basic` - 基本转换
- [ ] `test_markdown_to_storage_complex` - 复杂格式
- [ ] `test_storage_to_markdown_basic` - 反向转换
- [ ] `test_adapter_get_page_markdown` - Adapter 查询
- [ ] `test_adapter_create_page_markdown` - Adapter 创建

### 8.2 集成测试

- [ ] `test_create_page_with_markdown` - Markdown 创建
- [ ] `test_update_page_with_markdown` - Markdown 更新
- [ ] `test_get_page_returns_markdown` - Markdown 查询
- [ ] `test_backward_compatibility_storage` - 兼容 Storage
- [ ] `test_search_returns_excerpt` - 搜索返回 excerpt
- [ ] `test_update_version_conflict` - version 冲突处理

### 8.3 边界测试

- [ ] 空内容
- 超大文档 (>100KB)
- 特殊字符
- 嵌套结构
- 不支持的宏元素

## 9. 当前行为

当前实现使用函数参数控制格式:

```python
# 查询 - 使用 format 参数
confluence_get_page(page_id, format="markdown")  # 默认返回 Markdown

# 创建/更新 - 使用 body_format 参数
confluence_create_page(space_key, title, body, body_format="markdown")
confluence_update_page(page_id, body, body_format="markdown")

# 截断 - 使用 max_chars 参数
confluence_get_page(page_id, max_chars=10000)
```

**默认值:**
- `format`: "markdown"
- `body_format`: "markdown"
- `max_chars`: 无限制

> 设计决策: 使用函数参数控制格式和限制，而非 config.yaml。这样更灵活，调用方可根据需要调整。

## 10. 里程碑

- [x] M1: 搭建 adapter.py 框架
- [x] M2: 实现内置转换器 (替代 confluence-markdown-exporter)
- [x] M3: 实现内置转换器 (替代 markdown-to-confluence)
- [x] M4: 更新 confluence_get_page 支持 format 参数
- [x] M5: 更新 confluence_create_page 支持 body_format 参数
- [x] M6: 更新 confluence_update_page 支持 body_format 参数
- [x] M7: 更新 Tool Schema
- [x] M8: 单元测试 + 集成测试
- [x] M9: 文档更新

## 11. 依赖

```txt
# 当前实现使用内置转换器，无需额外依赖
# 如需使用外部库，可安装:
# markdown-to-confluence>=0.4.0  # 注意: 需要复杂配置，不适合简单转换
```

## 12. 替代方案

当前实现使用正则表达式自实现转换器，支持:
- Headers, Bold/Italic/Strike
- Code blocks / inline code
- Links / Images
- Lists (ul/ol)
- Tables
- Horizontal rules

如需更强大的转换能力，可考虑:
- 使用 pandoc 命令行转换
- 自定义实现完整转换器
