# 文件上传与多模态支持 - 需求文档

## 背景

用户需要上传多种格式的文件（图片、PDF、Word、Excel、CSV等），并让 LLM 能够分析和处理这些文件内容。

## 设计原则：两层架构

```
┌─────────────────────────────────────────────┐
│           统一中间格式 (IR)                  │
│   Markdown + JSON Blocks + Metadata          │
└─────────────────────────────────────────────┘
                     ▲
┌─────────────────────────────────────────────┐
│           专用解析器层                         │
│  docx │ PDF │ Excel │ CSV │ Image          │
└─────────────────────────────────────────────┘
```

## 1. 统一中间格式 (IR)

### 目标
"可追溯 + 可分块 + 结构保留"，而不是单纯转成一大坨纯文本。

### 格式设计

#### Markdown (主要输出)
- 标题/段落/列表/代码块/表格
- 保持原有层级结构

#### JSON Metadata
```json
{
  "source": {
    "filename": "document.pdf",
    "file_id": "uuid",
    "file_type": "application/pdf",
    "created_at": "2026-02-28T09:00:00Z"
  },
  "pages": [
    {
      "page_number": 1,
      "blocks": [
        {
          "type": "heading",
          "level": 1,
          "content": "标题内容",
          " bbox": null
        },
        {
          "type": "paragraph",
          "content": "段落内容...",
          " bbox": null
        },
        {
          "type": "table",
          "rows": 3,
          "cols": 2,
          "markdown": "| A | B |\n|---|---|\n| 1 | 2 |",
          "json": [["A","B"],["1","2"]]
        }
      ]
    }
  ],
  "statistics": {
    "total_pages": 10,
    "total_tables": 3,
    "confidence": 0.95
  }
}
```

### 表格双轨制
- **Markdown 表格**: 给 LLM 阅读
- **JSON rows/cols**: 方便后续检索、对齐、比对

---

## 2. 各类型文件解析方案

### A. 图片 (png/jpg/gif/webp)

**策略选择**:
| 场景 | 方案 |
|------|------|
| 多模态 LLM | 直接发送图片 + 让模型输出结构化摘要 |
| 离线/纯文本模型 | OCR: PaddleOCR (中英) 或 Tesseract (轻量) |
| 复杂版面 (发票/表格) | OCR + 版面分析 (layout) |

**输出**:
```json
{
  "type": "image",
  "content": "markdown描述...",
  "blocks": [
    {"type": "text", "content": "...", "bbox": [x1,y1,x2,y2], "confidence": 0.9},
    {"type": "table", "markdown": "...", "json": [[...]]}
  ]
}
```

### B. Word (.docx)

**推荐**: python-docx
- 结构稳定，可读取段落、标题、表格

**备选**: mammoth
- 适合需要保留样式/脚注的场景

**输出**: Markdown (标题层级、列表) + 表格 (md+json)

### C. PDF

**必须先判断类型**:

| 类型 | 方案 |
|------|------|
| 文本型 PDF | PyMuPDF (fitz): 快、页码定位好 |
| 扫描型 PDF | 走图片 OCR / 多模态路线 |
| 表格提取 | pdfplumber 或 多模态模型 |

**输出**: Markdown + JSON blocks (每页带 page_number)

### D. CSV

**推荐**: pandas.read_csv()

**输出策略**:
1. 先做 profile: 列名、类型、缺失率、样例
2. 按需求抽样/分页/聚合
3. 输出: 列说明 + 前 N 行样例 + 统计摘要 (Markdown + JSON)

### E. Excel (.xlsx)

**推荐**: openpyxl + pandas

**策略**:
1. 读取每个 sheet
2. 识别"表格区域" (非空块)
3. 导出: 结构化 JSON + Markdown 预览
4. 公式处理: 保留"显示值"，公式放 metadata

---

## 3. API 设计

### 3.1 上传文件

```http
POST /api/files/upload
Content-Type: multipart/form-data

参数:
  - file: File (required)
  - folder: string (optional, default "uploads")

响应 200:
{
  "success": true,
  "file_id": "uuid",
  "filename": "document.pdf",
  "path": "/workspace/uploads/uuid/document.pdf",
  "size": 12345,
  "content_type": "application/pdf"
}
```

### 3.2 解析文件

```http
POST /api/files/parse
Content-Type: application/json

{
  "path": "/workspace/uploads/uuid/document.pdf",
  "options": {
    "include_images": true,
    "max_pages": 100,
    "table_format": "both"  // "markdown" | "json" | "both"
  }
}

响应 200:
{
  "success": true,
  "content_type": "application/pdf",
  "markdown": "# 文档标题\n\n第一段内容...",
  "json": {
    "source": {...},
    "pages": [...],
    "statistics": {...}
  }
}
```

### 3.3 预览 (仅 Markdown)

```http
GET /api/files/preview?path=/workspace/uploads/file.pdf&max_chars=5000

响应 200:
{
  "success": true,
  "preview": "markdown truncated content...",
  "truncated": true,
  "total_chars": 50000
}
```

---

## 4. 目录结构

```
src/
├── gateway/
│   └── webchat.py          # 上传 API
├── utils/
│   └── file_parser.py       # 统一解析入口
│       ├── parse_image()    # 图片解析
│       ├── parse_docx()    # Word 解析
│       ├── parse_pdf()     # PDF 解析
│       ├── parse_csv()     # CSV 解析
│       └── parse_excel()    # Excel 解析
└── workspace/
    └── uploads/            # 上传文件存储
```

---

## 5. 配置项

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
  
  parse:
    # 图片
    vision_enabled: true          # 优先使用多模态 LLM
    ocr_engine: "paddleocr"     # paddleocr | tesseract
    
    # PDF
    pdf_strategy: "auto"         # auto | text | ocr
    max_pages: 100
    
    # Excel/CSV
    max_rows: 10000
    include_formulas: false
```

---

## 6. 分块策略 (Chunking)

### 小文件
直接发送完整 Markdown

### 大文件
1. 先生成"目录/摘要/索引"
2. 按问题取相关块 (RAG/检索)

### 每块带 source 引用
```json
{
  "chunk_id": "page2_paragraph3",
  "content": "...",
  "source": {
    "file": "document.pdf",
    "page": 2,
    "bbox": [x1, y1, x2, y2]
  }
}
```

---

## 7. 实现计划

### Phase 1: 基础架构
- [ ] 统一 IR 格式定义
- [ ] 文件上传 API
- [ ] 基础文件解析入口

### Phase 2: 文档解析
- [ ] PDF 解析 (文本型)
- [ ] Word 解析
- [ ] Excel/CSV 解析

### Phase 3: 图片支持
- [ ] 图片上传 + 预览
- [ ] 多模态 LLM 集成
- [ ] OCR 备选

### Phase 4: 高级功能
- [ ] 分块检索
- [ ] 大文件 RAG
- [ ] 解析缓存

---

## 8. 依赖

```txt
# 核心
python-magic        # 文件类型检测

# 文档解析
PyMuPDF            # PDF 文本提取
pdfplumber         # PDF 表格提取
python-docx        # Word 解析

# 数据处理
pandas             # CSV/Excel 分析
openpyxl           # Excel 结构

# 图片
Pillow             # 图片处理
PaddleOCR          # OCR (备选)
```

---

## 9. 核心原则总结

| 原则 | 实现 |
|------|------|
| **可追溯** | 每块带 source (file, page, bbox) |
| **可分块** | 支持按页/段落/表格分块 |
| **结构保留** | Markdown 表格 + JSON 双轨 |
| **统一 IR** | 所有格式 → Markdown + JSON blocks |
