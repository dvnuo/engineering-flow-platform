# File Upload Implementation Design

## Overview

This document details the implementation design for file upload and parsing in the Engineering Flow Platform.

## 1. Architecture

```
┌─────────────┐    ┌──────────────┐    ┌─────────────────┐
│  Frontend   │───▶│  Gateway     │───▶│  File Parser    │
│  (Upload)   │    │  (API)       │    │  (Utils)        │
└─────────────┘    └──────────────┘    └─────────────────┘
                          │                      │
                          ▼                      ▼
                   ┌──────────────┐    ┌─────────────────┐
                   │  Upload Dir  │    │  LLM Integration│
                   │  (Storage)   │    │  (Image Send)  │
                   └──────────────┘    └─────────────────┘
```

## 2. Module Structure

```
src/
├── gateway/
│   └── webchat.py          # File upload API endpoints
├── utils/
│   └── file_parser/
│       ├── __init__.py     # Unified parse entry
│       ├── config.py       # File parser config
│       ├── models.py       # Data models (ParseResult, Block)
│       ├── image.py        # Image parser
│       ├── pdf.py          # PDF parser
│       ├── docx.py         # Word parser
│       ├── excel.py       # Excel parser
│       ├── csv.py         # CSV parser
│       └── validators.py  # File validation
└── workspace/
    └── uploads/            # Uploaded files storage
```

## 3. Data Models

### 3.1 Block Schema

```python
class Block(BaseModel):
    chunk_id: str                    # "file_page_row" format
    type: str                        # "heading", "paragraph", "table", "list", "image"
    content: str                     # Text content
    level: Optional[int] = None      # Heading level (1-6)
    markdown: Optional[str] = None   # Table markdown
    json: Optional[Any] = None       # Table JSON
    
    # Location
    page: Optional[int] = None      # PDF page number
    sheet: Optional[str] = None     # Excel sheet name
    row_range: Optional[str] = None # "1-10" format
    
    # Metadata
    method: str                      # "pymupdf", "pandas", "vision", "ocr"
    confidence: float = 1.0          # 0.0 - 1.0
    extracted_at: str                # ISO timestamp
```

### 3.2 Parse Result

```python
class ParseResult(BaseModel):
    success: bool
    content_type: str                # "image/jpeg", "application/pdf", etc.
    file_id: str                     # UUID
    filename: str
    
    # Content
    markdown: str                    # Full markdown
    blocks: List[Block] = []         # Structured blocks
    
    # Summary
    json: Dict[str, Any] = {}        # Source, pages, statistics
    
    # Metadata
    parse_time_ms: int
    error: Optional[str] = None
```

### 3.3 Image Constraints

```python
class ImageConstraints(BaseModel):
    max_count: int = 1               # Max images per LLM request
    max_size_mb: int = 3             # Max file size in MB
    allowed_formats: List[str] = ["jpg", "jpeg", "png", "webp", "gif"]
```

### 3.4 Schema Contract (Field Constraints)

**chunk_id 生成规则**:
- 格式: `{file_id}_{page}_{row}` 或 `{file_id}_{index}`
- 全局唯一: 使用 UUID 作为 file_id 保证唯一性
- 可复现: 相同文件 + 相同解析器 = 相同 chunk_id

**page 字段**:
- 1-based (PDF, Word 第一页 page=1)

**row_range 字段** (Excel/CSV):
- 1-based, 闭区间
- 示例: "1-10" 表示第1行到第10行

**confidence 字段**:
- 范围: 0.0 - 1.0
- OCR 默认 0.8，Vision 默认 0.9

## 4. API Endpoints

### 4.1 Upload File

```http
POST /api/files/upload
Content-Type: multipart/form-data

Response 201:
{
  "success": true,
  "file_id": "uuid-xxx",
  "filename": "document.pdf",
  "content_type": "application/pdf",
  "size": 1024000,
  "uploaded_at": "2026-02-28T14:00:00Z"
}
```

### 4.2 Parse File

```http
POST /api/files/parse
Content-Type: application/json

{
  "file_id": "uuid-xxx",
  "options": {
    "include_images": true,
    "max_pages": 100,
    "table_format": "both"
  }
}

Response 200:
{
  "success": true,
  "content_type": "application/pdf",
  "markdown": "...",
  "blocks": [...],
  "json": {...},
  "parse_time_ms": 1500
}
```

### 4.3 Preview File

```http
GET /api/files/{file_id}/preview?max_chars=5000

Response 200:
{
  "success": true,
  "preview": "markdown...",
  "truncated": true,
  "total_chars": 50000
}
```

### 4.4 List Files

```http
GET /api/files?session_id=xxx

Response 200:
{
  "files": [
    {"file_id": "xxx", "filename": "doc.pdf", "size": 1024, "uploaded_at": "..."}
  ]
}
```

### 4.5 Delete File

```http
DELETE /api/files/{file_id}

Response 200:
{"success": true}
```

## 5. File Validation

### 5.1 Size Check

```python
def validate_file_size(size: int, max_size_mb: int = 10) -> bool:
    return size <= max_size_mb * 1024 * 1024
```

### 5.2 Type Check

```python
ALLOWED_MIME_TYPES = {
    "image": ["image/jpeg", "image/png", "image/webp", "image/gif"],
    "document": ["application/pdf", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"],
    "spreadsheet": ["application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "text/csv"],
}

def validate_content_type(mime_type: str, allowed_types: List[str]) -> bool:
    # Check exact match or wildcard
    category = mime_type.split("/")[0]
    for allowed in allowed_types:
        if allowed == mime_type:
            return True
        if allowed.endswith("/*") and allowed.split("/")[0] == category:
            return True
    return False
```

### 5.3 Image Constraints Validation

```python
def validate_image_for_llm(file_path: str, constraints: ImageConstraints) -> Tuple[bool, str]:
    """Validate image can be sent to LLM."""
    
    # Check size
    size = os.path.getsize(file_path)
    if size > constraints.max_size_mb * 1024 * 1024:
        return False, f"File too large: {size / 1024 / 1024:.1f}MB > {constraints.max_size_mb}MB"
    
    # Check format
    ext = Path(file_path).suffix.lower().lstrip(".")
    if ext not in constraints.allowed_formats:
        return False, f"Unsupported format: {ext}"
    
    return True, ""
```

### 5.4 Filename Security

**⚠️ 禁止将用户传入的 filename 直接拼接到路径**

```python
import re

# 只允许安全字符
FILENAME_PATTERN = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9._-]{0,200}$')

def sanitize_filename(filename: str) -> str:
    """Sanitize user-provided filename.
    
    Rules:
    - 只允许字母、数字、点、下划线、连字符
    - 必须以字母或数字开头
    - 最大 200 字符
    - 去除控制字符
    """
    # 提取文件名（去除路径）
    name = Path(filename).name
    
    # 去除控制字符
    name = ''.join(c for c in name if ord(c) >= 32)
    
    # 检查是否合法
    if not FILENAME_PATTERN.match(name):
        # 不合法则使用随机名
        import uuid
        return f"file_{uuid.uuid4().hex[:8]}"
    
    return name

def get_safe_path(file_id: str, original_filename: str) -> Path:
    """Generate safe storage path.
    
    存盘名: {file_id}{ext}
    原始文件名: 只存入 metadata
    """
    ext = Path(original_filename).suffix.lower()
    # 验证扩展名
    if not re.match(r'^\.[a-z0-9]+$', ext):
        ext = ""
    
    return UPLOAD_DIR / f"{file_id}{ext}"
```

## 6. Image Parser Implementation

### 6.1 Strategy Selection

```python
async def parse_image(file_path: str, options: Dict) -> ParseResult:
    start = time.time()
    
    # Option 1: Vision LLM (if enabled and supported)
    if options.get("vision_enabled", True):
        try:
            result = await parse_image_with_vision(file_path)
            if result.success:
                return result
        except Exception as e:
            logger.warning(f"Vision parsing failed, falling back to OCR: {e}")
    
    # Option 2: OCR fallback
    result = await parse_image_with_ocr(file_path)
    result.parse_time_ms = int((time.time() - start) * 1000)
    return result
```

### 6.2 Vision LLM Parsing

```python
async def parse_image_with_vision(file_path: str) -> ParseResult:
    """Use multimodal LLM to describe image."""
    
    # Read image as base64
    with open(file_path, "rb") as f:
        image_b64 = base64.b64encode(f.read()).decode()
    
    # Call LLM with image
    response = await llm.chat([
        {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
                {"type": "text", "text": "Describe this image in detail. Extract all text, describe objects, and note any structure like tables."}
            ]
        }
    ])
    
    # Parse response into blocks
    blocks = [Block(
        chunk_id="image_1",
        type="paragraph",
        content=response.content,
        method="vision",
        confidence=0.9,
        extracted_at=datetime.now().isoformat()
    )]
    
    return ParseResult(
        success=True,
        content_type=mimetypes.guess_type(file_path)[0],
        file_id=file_id,
        filename=Path(file_path).name,
        markdown=f"# Image: {Path(file_path).name}\n\n{response.content}",
        blocks=blocks,
        parse_time_ms=0
    )
```

### 6.3 OCR Parsing

**⚠️ 不同 OCR 引擎返回结构不同，必须分别处理**

#### PaddleOCR 返回结构

```python
# ocr.ocr() 返回: [ [ [box, (text, confidence)], ... ], ... ]
# 外层列表每元素为一页，内层列表每元素为一行

# 示例:
results = ocr.ocr(file_path, cls=True)
# [
#   [  # Page 1
#     [ [[10,20],[50,20],[50,40],[10,40]], ("Hello", 0.95), ... ],
#     [ [[10,50],[80,50],[80,70],[10,70]], ("World", 0.92), ... ]
#   ]
# ]

def paddle_to_blocks(results: list) -> List[Block]:
    """Convert PaddleOCR results to blocks."""
    blocks = []
    for page_idx, page in enumerate(results):
        for line_idx, line in enumerate(page):
            box, (text, confidence) = line
            blocks.append(Block(
                chunk_id=f"image_{page_idx + 1}_{line_idx + 1}",
                type="paragraph",
                content=text,
                method="paddleocr",
                confidence=confidence,
                bbox=box,  # [[x1,y1],[x2,y2],[x3,y3],[x4,y4]]
                extracted_at=datetime.now().isoformat()
            ))
    return blocks
```

#### Tesseract 返回结构

```python
# pytesseract.image_to_data() 返回: Dict[str, List]
# keys: 'text', 'conf', 'left', 'top', 'width', 'height', ...

# 示例:
results = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
# {
#   'text': ['Hello', 'World', ''],
#   'conf': [95, 92, -1],
#   'left': [10, 10],
#   'top': [20, 50],
#   ...
# }

def tesseract_to_blocks(results: dict) -> List[Block]:
    """Convert Tesseract results to blocks."""
    blocks = []
    texts = results.get('text', [])
    confs = results.get('conf', [])
    lefts = results.get('left', [])
    tops = results.get('top', [])
    widths = results.get('width', [])
    heights = results.get('height', [])
    
    for idx, (text, conf) in enumerate(zip(texts, confs)):
        if not text.strip() or conf < 0:
            continue  # 跳过空文本
        
        blocks.append(Block(
            chunk_id=f"image_1_{idx + 1}",
            type="paragraph",
            content=text,
            method="tesseract",
            confidence=conf / 100,
            bbox=[
                [lefts[idx], tops[idx]],
                [lefts[idx] + widths[idx], tops[idx]],
                [lefts[idx] + widths[idx], tops[idx] + heights[idx]],
                [lefts[idx], tops[idx] + heights[idx]]
            ],
            extracted_at=datetime.now().isoformat()
        ))
    return blocks
```

#### 统一解析入口

```python
async def parse_image_with_ocr(file_path: str, options: Dict) -> ParseResult:
    """Use OCR to extract text from image."""
    
    engine = options.get("ocr_engine", "paddleocr")
    
    if engine == "paddleocr":
        from paddleocr import PaddleOCR
        ocr = PaddleOCR(use_angle_cls=True, lang='ch_en')
        raw_results = ocr.ocr(file_path, cls=True)
        blocks = paddle_to_blocks(raw_results)
    else:
        # Tesseract
        import pytesseract
        from PIL import Image
        img = Image.open(file_path)
        raw_results = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
        blocks = tesseract_to_blocks(raw_results)
    
    markdown = "\n".join(b.content for b in blocks if b.content.strip())
    
    return ParseResult(
        success=True,
        content_type=mimetypes.guess_type(file_path)[0],
        file_id=file_id,
        filename=Path(file_path).name,
        markdown=markdown,
        blocks=blocks,
        parse_time_ms=0
    )
```

## 7. LLM Image Integration

### 7.1 Image Preprocessing (Compression)

**⚠️ 重要：发送前必须压缩**

直接发送 base64 会导致：
- 体积膨胀 ~33%
- 容易触发 payload 限制
- 大图造成内存尖峰

**压缩策略**:
```python
from PIL import Image
import io
import base64

def compress_image_for_llm(
    file_path: str,
    max_dimension: int = 1024,  # 最长边
    quality: int = 80           # JPEG 质量
) -> str:
    """Compress image and return base64.
    
    Args:
        file_path: Original image path
        max_dimension: Max width or height in pixels
        quality: JPEG quality (70-85)
    
    Returns:
        Base64 encoded compressed image
    """
    with Image.open(file_path) as img:
        # Convert to RGB if needed
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        
        # Resize if needed (maintain aspect ratio)
        if max(img.size) > max_dimension:
            ratio = max_dimension / max(img.size)
            new_size = tuple(int(dim * ratio) for dim in img.size)
            img = img.resize(new_size, Image.Resampling.LANCZOS)
        
        # Compress to JPEG
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=quality, optimize=True)
        return base64.b64encode(buffer.getvalue()).decode()
```

**压缩配置**:
```yaml
files:
  parse:
    llm_image:
      max_dimension: 1024    # 最长边像素
      jpeg_quality: 80       # 70-85 推荐
```

### 7.2 Concurrency Limits

```yaml
files:
  parse:
    llm_image:
      max_concurrent: 2       # 最大并发请求数
      max_request_size_mb: 5 # 单次请求体上限（压缩后）
```

### 7.3 Sending Images to LLM

```python
async def send_images_to_llm(file_ids: List[str], llm_client) -> List[Dict]:
    """Send images to LLM with constraints."""
    
    constraints = get_image_constraints()  # From config
    
    if len(file_ids) > constraints.max_count:
        raise ValueError(f"Too many images: {len(file_ids)} > {constraints.max_count}")
    
    images_content = []
    for file_id in file_ids:
        file_path = get_file_path(file_id)
        
        # Validate
        valid, error = validate_image_for_llm(file_path, constraints)
        if not valid:
            raise ValueError(f"Image validation failed: {error}")
        
        # Compress before encoding (critical for performance)
        image_b64 = compress_image_for_llm(
            file_path,
            max_dimension=config.llm_image.max_dimension,
            quality=config.llm_image.jpeg_quality
        )
        
        images_content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}
        })
    
    return images_content
```

## 8. Configuration

### 8.1 Config Schema

```yaml
files:
  enabled: true
  upload_dir: "~/.efp/workspace/uploads"
  max_size_mb: 10
  
  # File type restrictions
  allowed_types:
    - image/jpeg
    - image/png
    - image/webp
    - image/gif
    - application/pdf
    - application/vnd.openxmlformats-officedocument.wordprocessingml.document
    - application/vnd.openxmlformats-officedocument.spreadsheetml.sheet
    - text/csv
  
  # Parser settings
  parse:
    vision_enabled: true
    ocr_engine: "paddleocr"
    
    # Image LLM constraints
    llm_image:
      max_count: 1
      max_size_mb: 3
      allowed_formats:
        - jpg
        - jpeg
        - png
        - webp
        - gif
    
    # PDF settings
    pdf_strategy: "auto"
    max_pages: 100
    
    # Excel/CSV
    max_rows: 10000
```

## 9. Storage

### 9.1 File Storage

**⚠️ 安全原则：禁止 glob 模糊匹配，使用 metadata 映射**

```python
UPLOAD_DIR = Path("~/.efp/workspace/uploads").expanduser()

# 内存缓存 (生产环境可用 Redis)
_file_metadata: Dict[str, Dict] = {}

def register_file(file_id: str, original_filename: str, stored_filename: str, 
                  content_type: str, size: int) -> None:
    """Register file metadata."""
    _file_metadata[file_id] = {
        "file_id": file_id,
        "original_filename": original_filename,  # 用户原始文件名（仅存 metadata）
        "stored_filename": stored_filename,        # 服务端存储文件名
        "content_type": content_type,
        "size": size,
        "uploaded_at": datetime.now().isoformat()
    }

def get_file_path(file_id: str) -> Path:
    """Get file path by ID (from metadata, not glob).
    
    ⚠️ 不要用 glob 模糊匹配，可能匹配到意外文件
    """
    if file_id not in _file_metadata:
        raise FileNotFoundError(f"File not found: {file_id}")
    
    stored_name = _file_metadata[file_id]["stored_filename"]
    return UPLOAD_DIR / stored_name

def get_metadata(file_id: str) -> Dict:
    """Get file metadata."""
    return _file_metadata.get(file_id)

async def save_uploaded_file(file_id: str, content: bytes, original_filename: str) -> Path:
    """Save uploaded file to storage.
    
    存盘名: {file_id}{ext} (服务端生成，不使用用户输入)
    """
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    
    # 提取安全扩展名
    ext = Path(original_filename).suffix.lower()
    if not re.match(r'^\.[a-z0-9]+$', ext):
        ext = ""  # 无效扩展名则不使用
    
    # 服务端生成存储名
    stored_filename = f"{file_id}{ext}"
    file_path = UPLOAD_DIR / stored_filename
    
    # 写入文件
    file_path.write_bytes(content)
    
    # 注册 metadata
    content_type = magic.from_buffer(content[:1024], mime=True)
    register_file(
        file_id=file_id,
        original_filename=original_filename,
        stored_filename=stored_filename,
        content_type=content_type,
        size=len(content)
    )
    
    return file_path
```

### 9.2 File Metadata

Store in session or separate JSON:

```json
{
  "files": {
    "uuid-xxx": {
      "filename": "document.pdf",
      "content_type": "application/pdf",
      "size": 1024000,
      "uploaded_at": "2026-02-28T14:00:00Z",
      "session_id": "session-xxx"
    }
  }
}
```

## 10. Error Handling

### 10.1 Error Types

```python
class FileError(Exception):
    """Base file error."""
    pass

class FileTooLargeError(FileError):
    """File exceeds size limit."""
    pass

class UnsupportedFileTypeError(FileError):
    """File type not allowed."""
    pass

class FileNotFoundError(FileError):
    """File not found."""
    pass

class ParseError(FileError):
    """Failed to parse file."""
    pass
```

### 10.2 Error Responses

```python
@app.exception_handler(FileError)
async def handle_file_error(request, exc):
    return json_response(
        {"success": false, "error": str(exc), "error_type": exc.__class__.__name__},
        status=400
    )
```

## 11. Tests Design

### 11.1 Unit Tests

```python
# tests/test_file_parser.py

class TestValidators:
    """Test file validation."""
    
    def test_validate_file_size_pass(self):
        """Test valid file size passes."""
        assert validate_file_size(1024, 10) is True
    
    def test_validate_file_size_fail(self):
        """Test oversized file fails."""
        assert validate_file_size(11 * 1024 * 1024, 10) is False
    
    def test_validate_content_type_exact(self):
        """Test exact type match."""
        assert validate_content_type("image/jpeg", ["image/jpeg"]) is True
    
    def test_validate_content_type_wildcard(self):
        """Test wildcard match."""
        assert validate_content_type("image/png", ["image/*"]) is True
    
    def test_validate_image_for_llm_size(self):
        """Test image size validation."""
        # Create temp file
        with tempfile.NamedTemporaryFile(suffix=".jpg") as f:
            f.write(b"x" * (4 * 1024 * 1024))  # 4MB
            f.flush()
            
            valid, error = validate_image_for_llm(f.name, ImageConstraints())
            assert valid is False
            assert "too large" in error.lower()
    
    def test_validate_image_for_llm_format(self):
        """Test image format validation."""
        with tempfile.NamedTemporaryFile(suffix=".bmp") as f:
            valid, error = validate_image_for_llm(f.name, ImageConstraints())
            assert valid is False
            assert "unsupported" in error.lower()


class TestImageParser:
    """Test image parsing."""
    
    @pytest.mark.asyncio
    async def test_parse_jpeg_success(self, tmp_path):
        """Test successful JPEG parsing."""
        # Create test image
        img = Image.new("RGB", (100, 100), color="red")
        img_path = tmp_path / "test.jpg"
        img.save(img_path)
        
        result = await parse_image(str(img_path), {})
        
        assert result.success is True
        assert result.content_type == "image/jpeg"
        assert len(result.blocks) > 0
    
    @pytest.mark.asyncio
    async def test_parse_unsupported_format(self, tmp_path):
        """Test unsupported format handling."""
        img_path = tmp_path / "test.bmp"
        img = Image.new("RGB", (100, 100))
        img.save(img_path)
        
        result = await parse_image(str(img_path), {})
        
        assert result.success is False
        assert "unsupported" in result.error.lower()


class TestBlockSchema:
    """Test block data model."""
    
    def test_block_creation(self):
        """Test Block model creation."""
        block = Block(
            chunk_id="test_1",
            type="paragraph",
            content="Test content",
            method="vision",
            confidence=0.9
        )
        
        assert block.chunk_id == "test_1"
        assert block.type == "paragraph"
        assert block.confidence == 0.9
    
    def test_block_with_location(self):
        """Test Block with location data."""
        block = Block(
            chunk_id="pdf_1_5",
            type="paragraph",
            content="Page 5 content",
            page=5,
            method="pymupdf",
            extracted_at="2026-02-28T14:00:00Z"
        )
        
        assert block.page == 5
        assert block.chunk_id == "pdf_1_5"
```

### 11.2 Integration Tests

```python
# tests/test_file_api.py

class TestFileUploadAPI:
    """Test file upload API."""
    
    @pytest.mark.asyncio
    async def test_upload_valid_file(self, app, client):
        """Test uploading valid file."""
        content = b"test content"
        files = {"file": ("test.txt", content, "text/plain")}
        
        response = await client.post("/api/files/upload", data=files)
        
        assert response.status == 201
        data = await response.json()
        assert data["success"] is True
        assert "file_id" in data
    
    @pytest.mark.asyncio
    async def test_upload_oversized_file(self, app, client):
        """Test uploading oversized file."""
        content = b"x" * (11 * 1024 * 1024)  # 11MB
        files = {"file": ("large.txt", content, "text/plain")}
        
        response = await client.post("/api/files/upload", data=files)
        
        assert response.status == 400
        data = await response.json()
        assert data["success"] is False
        assert "too large" in data["error"].lower()
    
    @pytest.mark.asyncio
    async def test_upload_invalid_type(self, app, client):
        """Test uploading invalid file type."""
        content = b"malicious"
        files = {"file": ("malware.exe", content, "application/x-executable")}
        
        response = await client.post("/api/files/upload", data=files)
        
        assert response.status == 400


class TestFileParseAPI:
    """Test file parsing API."""
    
    @pytest.mark.asyncio
    async def test_parse_image(self, app, client, tmp_path):
        """Test parsing image file."""
        # Upload first
        img = Image.new("RGB", (100, 100), color="blue")
        img_path = tmp_path / "test.jpg"
        img.save(img_path)
        
        with open(img_path, "rb") as f:
            files = {"file": ("test.jpg", f.read(), "image/jpeg")}
            upload_resp = await client.post("/api/files/upload", data=files)
        
        file_id = (await upload_resp.json())["file_id"]
        
        # Parse
        parse_resp = await client.post(
            "/api/files/parse",
            json={"file_id": file_id}
        )
        
        assert parse_resp.status == 200
        data = await parse_resp.json()
        assert data["success"] is True
        assert "markdown" in data
```

### 11.3 Test Fixtures

```python
# tests/conftest.py

@pytest.fixture
def upload_dir(tmp_path):
    """Create temporary upload directory."""
    return tmp_path / "uploads"

@pytest.fixture
def sample_image(tmp_path):
    """Create sample image for testing."""
    img_path = tmp_path / "sample.jpg"
    img = Image.new("RGB", (200, 200), color="green")
    img.save(img_path, "JPEG")
    return str(img_path)

@pytest.fixture
def sample_pdf(tmp_path):
    """Create sample PDF for testing."""
    pdf_path = tmp_path / "sample.pdf"
    # Use reportlab or similar to create PDF
    return str(pdf_path)
```

## 12. Implementation Phases

### Phase 1: Foundation (1-2 days)
- [ ] File upload API endpoint
- [ ] Basic file storage
- [ ] File validation
- [ ] Unit tests for validators

### Phase 2: Image Parsing (1-2 days)
- [ ] Image parser module
- [ ] Vision LLM integration
- [ ] OCR fallback
- [ ] Image constraint validation

### Phase 3: Document Parsing (2-3 days)
- [ ] PDF parser
- [ ] Word parser
- [ ] Excel/CSV parser

### Phase 4: Integration (1 day)
- [ ] Integrate with LLM agent
- [ ] Frontend file upload
- [ ] End-to-end tests

## 13. Dependencies

```txt
# requirements.txt

# Image processing
Pillow>=10.0.0
pytesseract>=0.3.10
paddleocr>=2.7.0
paddlepaddle>=2.5.0

# Document processing
PyMuPDF>=1.23.0
python-docx>=1.1.0
openpyxl>=3.1.0
pandas>=2.0.0

# Validation
python-magic>=0.4.27
```

## 14. Security Considerations

1. **Path Traversal**: Use UUID for file_id, never expose filesystem paths
2. **File Type**: Validate MIME type, not just extension
3. **File Size**: Enforce limits before reading file content
4. **Storage**: Keep uploads in isolated directory with restricted permissions
5. **Cleanup**: Implement automatic cleanup of old uploaded files
