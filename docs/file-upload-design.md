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

```python
async def parse_image_with_ocr(file_path: str) -> ParseResult:
    """Use OCR to extract text from image."""
    
    # Use PaddleOCR or Tesseract
    engine = options.get("ocr_engine", "paddleocr")
    
    if engine == "paddleocr":
        from paddleocr import PaddleOCR
        ocr = PaddleOCR(use_angle_cls=True, lang='ch_en')
        results = ocr.ocr(file_path, cls=True)
    else:
        # Tesseract fallback
        import pytesseract
        from PIL import Image
        img = Image.open(file_path)
        results = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
    
    # Convert to blocks
    blocks = []
    for idx, line in enumerate(results):
        blocks.append(Block(
            chunk_id=f"image_1_{idx}",
            type="paragraph",
            content=line["text"],
            method=engine,
            confidence=line.get("confidence", 0) / 100,
            extracted_at=datetime.now().isoformat()
        ))
    
    markdown = "\n".join(b.content for b in blocks)
    
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

### 7.1 Sending Images to LLM

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
        
        # Read and encode
        with open(file_path, "rb") as f:
            image_b64 = base64.b64encode(f.read()).decode()
        
        ext = Path(file_path).suffix.lower().lstrip(".")
        mime_type = f"image/{ext}"
        if ext == "jpg":
            mime_type = "image/jpeg"
        
        images_content.append({
            "type": "image_url",
            "image_url": {"url": f"data:{mime_type};base64,{image_b64}"}
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

```python
UPLOAD_DIR = Path("~/.efp/workspace/uploads").expanduser()

def get_file_path(file_id: str, filename: str = None) -> Path:
    """Get file path by ID or filename."""
    if filename:
        return UPLOAD_DIR / f"{file_id}_{filename}"
    # Find file with matching ID
    for f in UPLOAD_DIR.glob(f"{file_id}_*"):
        return f
    raise FileNotFoundError(f"File not found: {file_id}")

async def save_uploaded_file(file_id: str, content: bytes, filename: str) -> Path:
    """Save uploaded file to storage."""
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    file_path = UPLOAD_DIR / f"{file_id}_{filename}"
    file_path.write_bytes(content)
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
