# 文件上传与多模态支持 - 需求文档

## 背景

用户需要上传多种格式的文件（图片、PDF、Word、Excel、CSV等），并让 LLM 能够分析和处理这些文件内容。

## 当前状态

- Engineering Flow 已有文件浏览/读取 API (`/api/files`)
- 尚无文件上传 API
- GPT-5-mini 支持多模态（图片 + 文本）

## 功能需求

### 1. 文件上传 API

**端点**: `POST /api/files/upload`

```python
# 请求
Content-Type: multipart/form-data
参数:
  - file: 文件内容
  - path: 上传目录 (可选, 默认 /workspace/uploads)

# 响应
{
  "success": true,
  "file_id": "uuid",
  "filename": "example.png",
  "path": "/workspace/uploads/example.png",
  "size": 1024,
  "content_type": "image/png"
}
```

### 2. 支持的文件格式

| 格式 | 处理方式 | 库 |
|------|----------|-----|
| 图片 (png, jpg, gif, webp) | 转为 base64 直接发送给 LLM | PIL |
| PDF | 提取文本 | PyPDF2 / pdfplumber |
| Word (docx) | 提取文本 | python-docx |
| Excel (xlsx) | 转为 Markdown 表格 | pandas |
| CSV | 转为 Markdown 表格 | pandas |
| PPT (pptx) | ❌ 暂不支持 | - |
| 纯文本 (txt, md, json) | 直接读取 | - |

### 3. 用户交互流程

```
1. 用户在 WebChat 点击"上传文件"按钮
2. 选择本地文件
3. 文件上传到 /workspace/uploads/
4. 系统自动解析文件内容
5. 解析后的内容附加到用户消息
6. 发送给 LLM 处理
```

### 4. 技术架构

```
上传文件
    ↓
/api/files/upload (保存到磁盘)
    ↓
文件解析器 (根据类型选择解析方式)
    ↓
内容转换 (文本/Base64)
    ↓
附加到消息 → 发送给 LLM
```

## API 设计

### 4.1 上传文件

```http
POST /api/files/upload
Content-Type: multipart/form-data

参数:
  - file: File (required)
  - folder: string (optional, default "uploads")

响应 200:
{
  "success": true,
  "file_id": "550e8400-e29b-41d4-a716-446655440000",
  "filename": "document.pdf",
  "path": "/workspace/uploads/document.pdf",
  "size": 12345,
  "content_type": "application/pdf",
  "processed": true,
  "text_preview": "第一页文本内容..."
}
```

### 4.2 解析文件

```http
POST /api/files/parse
Content-Type: application/json

{
  "path": "/workspace/uploads/document.pdf"
}

响应 200:
{
  "success": true,
  "content_type": "application/pdf",
  "text": "提取的文本内容...",
  "images": ["base64 encoded images..."],
  "markdown": "## 文档内容\n\n..."
}
```

### 4.3 列出上传的文件

```http
GET /api/files/list?folder=uploads

响应 200:
{
  "files": [
    {
      "file_id": "uuid",
      "filename": "image.png",
      "path": "/workspace/uploads/image.png",
      "size": 1024,
      "created_at": "2026-02-28T09:00:00Z"
    }
  ]
}
```

### 4.4 删除上传的文件

```http
DELETE /api/files?file_id=uuid

响应 200:
{
  "success": true
}
```

## 目录结构

```
src/
├── gateway/
│   └── webchat.py          # 新增上传 API
├── utils/
│   └── file_parser.py       # 新增: 文件解析模块
│   ├── parse_image()       # 解析图片 → base64
│   ├── parse_pdf()         # 解析 PDF → 文本
│   ├── parse_docx()        # 解析 Word → 文本
│   ├── parse_excel()       # 解析 Excel → Markdown
│   ├── parse_csv()          # 解析 CSV → Markdown
└── static/
    └── uploads/            # 上传文件存储目录
```

## 配置项 (config.yaml)

```yaml
files:
  enabled: true
  upload_dir: "~/.efp/workspace/uploads"
  max_size_mb: 10
  allowed_types:
    - image/*
    - application/pdf
    - application/vnd.openxmlformats-officedocument.*
    - text/*
  # 解析设置
  parse:
    pdf_extract_images: true  # PDF 中提取图片
    max_pages: 100            # PDF 最大页数
    excel_max_rows: 10000     # Excel 最大行数
```

## 安全考虑

1. **文件大小限制**: 默认 10MB
2. **文件类型限制**: 白名单机制
3. **文件名消毒**: 防止路径遍历攻击
4. **存储位置**: 仅允许在 workspace 目录内

## 实现计划

### Phase 1: 基础功能
- [ ] 文件上传 API
- [ ] 图片解析 (base64)
- [ ] 文本文件解析

### Phase 2: 文档解析
- [ ] PDF 解析
- [ ] Word 解析
- [ ] Excel/CSV 解析

### Phase 3: 高级功能
- [ ] WebSocket 流式上传
- [ ] 解析进度回调

## 依赖

```txt
python-magic        # 文件类型检测
Pillow             # 图片处理
PyPDF2             # PDF 解析
python-docx        # Word 解析
pandas             # Excel/CSV 解析
```

## 待讨论

1. 上传文件是否需要持久化？还是仅临时存储？
2. 是否需要支持 OCR (图片中的文字提取)?
3. 解析结果如何缓存？
