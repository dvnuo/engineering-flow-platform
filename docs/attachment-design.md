# 统一附件处理模块设计文档

## 1. 需求背景

当前系统存在多个处理附件的场景：
- **Webchat** - 用户上传文件（图片、文档等）
- **Jira** - Issue 附件
- **Confluence** - 页面附件

每个场景都需要：
1. 保存文件
2. 解析内容（文本提取/图片处理）
3. 转换为 LLM 可理解的格式

## 2. 目标

创建统一的公共模块 `src/utils/attachment.py`，复用现有 `src/utils/file_parser/` 的能力：

- ✅ 统一接口处理外部附件（Jira/Confluence）
- ✅ 复用现有文件存储和解析逻辑
- ✅ 支持多种文件类型（图片、文本、文档）

## 3. 现有能力分析

### 3.1 src/utils/file_parser/ (已实现)

| 函数 | 功能 | 复用性 |
|------|------|---------|
| `save_uploaded_file(content, filename, session_id)` | 保存文件 | ✅ 可直接调用 |
| `parse_file(file_id)` | 解析文本/PDF/DOCX/Excel | ✅ 可直接调用 |
| `get_image_for_llm(file_path)` | 图片转 base64 | ✅ 可直接调用 |
| `compress_image_for_llm(file_path)` | 图片压缩 | ✅ 可直接调用 |
| `get_file_path(file_id)` | 获取文件路径 | ✅ 可直接调用 |

### 3.2 src/gateway/webchat.py (文件下载 API)

```
GET /api/files/{file_id}  → 返回文件内容
```

## 4. 设计方案

### 4.1 新增模块

```
src/utils/attachment.py
```

### 4.2 核心接口

```python
async def download_and_process_attachment(
    url: str,
    session_id: str = None,
    options: dict = None
) -> AttachmentResult:
    """从外部URL下载附件，处理后返回给LLM
    
    Args:
        url: 附件URL (Jira/Confluence等)
        session_id: 会话ID
        options: 处理选项
            - include_image_data: bool = True  # 返回图片base64
            - max_image_size: int = 1024      # 图片最大尺寸
            - max_text_chars: int = 5000       # 文本最大字符
    
    Returns:
        AttachmentResult:
            - file_id: str
            - content_type: str
            - content: str  # base64 (图片) 或 文本内容
            - content_format: "base64" | "text"
            - metadata: dict
    """
```

### 4.3 内部函数

```python
async def _download_file(url: str) -> tuple[bytes, str]:
    """下载文件，返回(内容, content_type)"""

async def process_for_llm(
    content: bytes,
    content_type: str,
    options: dict = None
) -> AttachmentResult:
    """根据文件类型处理内容"""
```

### 4.4 使用方 (待实现)

| 模块 | 改动 |
|------|------|
| `src/jira/__init__.py` | 调用 `download_and_process_attachment` 处理 issue 附件 |
| `src/confluence/__init__.py` | 同上 |

## 5. 处理流程

```
外部URL (Jira/Confluence)
       ↓
download_and_process_attachment()
       ↓
┌─────────────────────────────────────┐
│  1. 下载文件 (requests/httpx)       │
│  2. 检测 MIME type                   │
│  3. save_uploaded_file() 保存        │
│  4. 根据类型处理:                    │
│     - 图片: get_image_for_llm()     │
│     - 文本: parse_file()            │
│     - 其他: 描述+URL                 │
└─────────────────────────────────────┘
       ↓
返回 AttachmentResult
       ↓
LLM 可理解的内容
```

## 6. 文件结构

```
src/utils/
├── __init__.py
├── file_parser/          # 现有 - 文件存储和解析
│   ├── __init__.py
│   ├── storage.py      # save_uploaded_file, get_file_path
│   ├── image.py        # 图片处理
│   └── ...
└── attachment.py      # 新增 - 统一附件处理
    ├── download_and_process_attachment()
    ├── _download_file()
    └── process_for_llm()
```

## 7. 改动清单

### 7.1 新增文件
- [ ] `src/utils/attachment.py`

### 7.2 修改文件
- [ ] `src/jira/__init__.py` - 处理 issue 附件
- [ ] `src/confluence/__init__.py` - 处理页面附件

## 8. 测试用例

```python
# 测试 Jira 附件
url = "https://company.atlassian.net/issue/PROJ-123/attachment"
result = await download_and_process_attachment(url)
assert result.content_type.startswith("image/")
assert result.content_format == "base64"

# 测试文本文件
url = "https://company.atlassian.net/issue/PROJ-123/attachment"
result = await download_and_process_attachment(url)
assert result.content_format == "text"
```

## 9. 优先级

1. **P0** - 创建 `src/utils/attachment.py` 核心模块
2. **P1** - Jira 集成（Issue 附件处理）
3. **P2** - Confluence 集成（页面附件处理）
