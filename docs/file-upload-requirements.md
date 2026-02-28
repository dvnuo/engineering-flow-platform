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
          "bbox": null
        },
        {
          "type": "paragraph",
          "content": "段落内容...",
          "bbox": null
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

**LLM 发送限制**:
- 每次只能发送 1 张图片
- 支持格式: jpg, png, webp, gif
- 文件大小上限: 3MB

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
  "file_id": "uuid",  // ✅ 安全: 只接受 file_id
  "options": {
    "include_images": true,
    "max_pages": 100,
    "table_format": "both"
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
GET /api/files/uuid/preview?max_chars=5000

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
└── utils/
    └── file_parser/        # 文件解析包
        ├── __init__.py     # 统一解析入口
        ├── image.py        # 图片解析
        ├── pdf.py          # PDF 解析
        ├── docx.py         # Word 解析
        └── excel.py        # Excel/CSV 解析
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
    
    # 图片 LLM 发送限制 (configurable)
    llm_image:
      max_count: 1                # 每次只能发送 1 张
      max_size_mb: 3              # 最大 3MB
      allowed_formats:             # 支持格式
        - jpg
        - jpeg
        - png
        - webp
        - gif
    
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

---

## 10. 安全要求

### 10.1 路径安全
- ❌ 禁止直接接受 path 参数 (防路径穿越)
- ✅ 只接受 file_id，服务端查表映射真实路径

```http
# ❌ 危险
POST /api/files/parse?path=/etc/passwd

# ✅ 安全
POST /api/files/parse
{"file_id": "uuid-xxx"}
```

### 10.2 文件安全
- 文件大小限制: max_size_mb
- 文件类型白名单: allowed_types
- 恶意文件扫描 (可选扩展)
- PII 敏感信息处理策略

### 10.3 鉴权与隔离
- 租户文件隔离
- 操作日志脱敏

---

## 11. 执行模型

### 11.1 同步 vs 异步
| 场景 | 模式 | 说明 |
|------|------|------|
| 小文件 (<1MB) | 同步 | 立即返回 |
| 大文件 (>1MB) | 异步 | 返回 job_id |
| OCR/复杂解析 | 异步 | 耗时较长 |

### 11.2 异步任务 API
```http
# 提交解析任务
POST /api/files/parse
{"file_id": "uuid"}

响应 202:
{"job_id": "job-xxx", "status": "queued"}

# 查询状态
GET /api/jobs/job-xxx

响应 200:
{"job_id": "job-xxx", "status": "completed", "result": {...}}

# 获取结果
GET /api/files/uuid/result

响应 200:
{"markdown": "...", "json": {...}}
```

---

## 12. Blocks Schema 规范

### 12.1 Block 结构
```json
{
  "chunk_id": "file_page_row",        // 必填: 块唯一ID
  "type": "heading|paragraph|table|list|image",  // 必填: 类型枚举
  "content": "文本内容",              // 必填: 文本
  "level": 1,                         // heading 专属
  "markdown": "| A | B |...",       // table 专属
  "json": [["A","B"],["1","2"]],     // table 专属
  
  "location": {                        // 定位信息
    "page": 1,                        // 页码 (PDF)
    "sheet": "Sheet1",               // Sheet 名 (Excel)
    "row_range": "1-10",             // 行范围 (CSV/Excel)
    "bbox": [x1,y1,x2,y2]            // 坐标 (可选, 图片/PDF)
  },
  
  "metadata": {
    "method": "pymupdf|pandas|vision", // 提取方法
    "confidence": 0.95,               // 置信度
    "extracted_at": "2026-02-28T..."
  }
}
```

### 12.2 定位能力分级
| 类型 | page | sheet | row_range | bbox |
|------|------|-------|-----------|------|
| PDF 文本 | ✅ | N/A | N/A | 可选 |
| PDF 扫描 | ✅ | N/A | N/A | ✅ |
| Word | ✅ | N/A | N/A | ❌ |
| Excel | N/A | ✅ | ✅ | ❌ |
| CSV | N/A | N/A | ✅ | ❌ |
| Image | N/A | N/A | N/A | ✅ |

---

## 13. PDF 表格提取策略

### 优先级
1. **文本表格** (line-based): 用 pdfplumber 规则提取
2. **视觉表格**: 多模态模型重建表格
3. **OCR 回退**: PaddleOCR 处理

### 输出标记
```json
{
  "type": "table",
  "markdown": "...",
  "json": [[...]],
  "metadata": {
    "extraction_method": "text_rules|vision|ocr",
    "confidence": 0.85
  }
}
```

---

## 14. LLM 输入预算策略

### 14.1 Token 预算
| 文件类型 | 最大输入 | 策略 |
|----------|----------|------|
| 小文件 | < 50K tokens | 直接发送 |
| 大文件 | 分块 + RAG | 先摘要后详情 |

### 14.2 表格抽样策略
- **CSV/Excel**: 列说明 + 前 20 行样例 + 统计摘要
- **大表格**: 分页发送，每页 < 100 行

### 14.3 默认流程
```
1. 文件解析 → Markdown + JSON
2. 内容 < 预算? → 直接发送
3. 内容 > 预算? → 生成摘要/索引 → 按需加载相关块
```

---

## 15. 可观测性

### 15.1 解析指标
- 解析耗时 (ms)
- 失败原因分类
- 各解析器成功率

### 15.2 日志示例
```json
{
  "event": "file_parse",
  "file_id": "uuid",
  "parser": "pdf_plumber",
  "duration_ms": 1500,
  "status": "success|failed",
  "error": "timeout|invalid|...",
  "pages": 10,
  "tables": 3
}
```

---

## 16. 缓存与去重

### 16.1 文件哈希
- 同一文件 (SHA256 相同) → 直接复用解析结果
- 缓存键: `{file_hash}_{parser_version}`

### 16.2 缓存 API
```http
GET /api/files/uuid/cached-result

响应 200:
{"cached": true, "result": {...}}
```

---

## 17. 8 条补强清单 (AC)

- [ ] parse/preview 不接受任意 path，改为 file_id（防路径穿越）
- [ ] 增加异步解析：POST /parse → job_id、GET /jobs/{id}
- [ ] blocks schema：必填字段、枚举、定位分级
- [ ] 明确 PDF 提取策略与回退链路（text → table rules → OCR/vision）
- [ ] 明确 LLM 输入预算与抽样策略
- [ ] 增加安全要求：鉴权、文件大小/类型白名单
- [ ] 增加可观测性：解析耗时、失败原因分类、成功率指标
- [ ] 增加缓存/去重：同一文件 hash 相同直接复用
