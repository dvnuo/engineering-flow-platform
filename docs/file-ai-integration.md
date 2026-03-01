# File AI Integration - Requirements

## Overview

Enable users to chat with AI about uploaded files. The AI should understand and discuss the content of uploaded files (images, PDFs, documents, etc.).

## Current State

### Working
- File upload via web UI
- File parsing (OCR, PDF text extraction, document parsing)
- File listing and management

### Not Working
- AI cannot access or discuss uploaded file content
- No context integration between file parsing and chat

## Requirements

### 1. File Context Storage (Two-Layer Architecture)

**Session Layer** - Lightweight metadata only:
```json
{
  "session_id": "xxx",
  "files": [
    {
      "file_id": "xxx",
      "filename": "document.pdf",
      "content_type": "application/pdf",
      "parse_status": "pending|processing|completed|failed",
      "parsed_at": "2026-03-01T12:00:00Z",
      "chunk_count": 42,
      "total_chars": 15000
    }
  ]
}
```

**Chunk Layer** - Parsed content stored separately:
```json
{
  "chunk_id": "xxx_pdf_1_001",
  "file_id": "xxx",
  "type": "paragraph|heading|table|image",
  "page": 1,
  "index": 1,
  "content": "Extracted text...",
  "markdown": "...",
  "table_json": [...],
  "confidence": 0.95,
  "source": "pymupdf|ocr|pandas|openpyxl|python-docx",
  "content_hash": "sha256...",
  "extracted_at": "2026-03-01T12:00:00Z"
}
```

**Chunk Deduplication:**
- Each chunk has `content_hash` for deduplication
- Deduplication only when same `source` AND same `content_hash`
- Different sources (OCR vs PDF text) are kept separate with source attribution

### 2. Auto-parse with Async Job Support

- Upload triggers parse job (large files: async with job_id)
- Parse status flow: `pending` → `processing` → `completed|failed`
- Build retrieval index after parse

### 3. Retrieval & Indexing Strategy

**Phase 1: Keyword Index**
- Simple inverted index on content
- Metadata filtering (file_id, page, type)

**Phase 2: Vector Index (Recommended)**
- **Embedding Model**: text-embedding-3-small or text-embedding-ada-002
- **Chunk Overlap**: 10-20% for context continuity
- **Search**: L2 distance / inner product (cosine similarity)
- **Hybrid Retrieval**: 
  - Keyword rank + semantic score fusion
  - Re-weighting: keyword match × 0.3 + semantic score × 0.7
- **Multilingual**: Use multilingual embedding model

**Chunk Size Rules:**
- Max chunk size: 800-1500 tokens
- Split by paragraph/table/page boundaries
- Overlap: 10-20% for context continuity

### 4. AI Context Injection (Default: RAG)

**Default Behavior** (RAG-style):
- User message → retrieve top-k relevant chunks → inject into prompt
- Budget controls:
  - `top_k`: 5-12 chunks
  - `max_chunk_size`: 800-1500 tokens
  - `max_total_tokens`: 4000-6000 for file context

**Token Budget Fallback Policy (Pseudo-code):**
```
function compute_context(query, session_files):
    chunks = retrieve_chunks(query, session_files, top_k=12)
    tokens = estimate_tokens(chunks)
    
    if tokens < 1000:
        return chunks, "direct"
    
    if tokens < 4000:
        return top_k(chunks, k=8), "top-k"
    
    if tokens < 8000:
        summary = summarize(chunks)
        refined = retrieve_chunks(query, session_files, top_k=5)
        return [summary] + refined, "summarize-then-retrieve"
    
    return error("Query too broad. Try focusing on specific files or sections.")

function retrieve_chunks(query, files, top_k):
    if hybrid_mode:
        kw_scores = keyword_search(query, files)
        vec_scores = vector_search(query, files)
        combined = fuse(kw_scores, vec_scores)
        return top_k(combined)
    else:
        return vector_search(query, files)
```

**Explicit Reference** (`@file_xxx`, `@all`):
- Bypasses RAG, retrieves all chunks from specified file(s)
- Only when file is small enough or user explicitly requests

### 5. File Reference Commands

**Syntax Rules:**
- `@file_xxx` - Reference specific file by ID
- `@last` - Reference last uploaded file
- `@all` - Include all files in session
- `@chunk_xxx` - Reference specific chunk (from UI selection)
- Combined: `@file_A @chunk_xxx @last`

**Syntax Priority Table:**
| Syntax | Meaning | Priority | Result |
|--------|---------|----------|--------|
| `@chunk_xxx` | Specific chunk | 1 | Only those chunks |
| `@file_xxx` | All chunks of file | 2 | Union of file chunks |
| `@last` | Last uploaded file | 3 | Last file's chunks |
| `@all` | All session files | 4 | All chunks (override by above) |

**Combination Logic:**
- **Union**: Multiple `@file` refs = union of all chunks
- **Override**: Specific files override `@all`
- **Priority**: `@chunk` > `@file` > `@last` > `@all`

**Error Handling:**
- File not found: `{error: "File not found", available_files: [...], suggestions: [...]}`
- Parse pending: `{warning: "File is still parsing", progress: 50%, retry_after: 5s}`
- Parse failed: `{error: "Parse failed", reason: "...", action: "re-parse"}`
- No relevant chunks: `{warning: "No relevant content found", suggestions: ["Try a broader query", "Use @all to include all content"]}`

### 6. Image/Vision Content Handling

- Image chunks include OCR text content
- **Caption Strategy**: 
  - Default: Use raw OCR text (fast, reliable)
  - Opt-in: LLM-generated caption for richer context
- Image embeddings stored separately (opt-in for vector search)
- Images excluded from search by default (`include_images: false`)
- Image metadata: dimensions, format, page location

### 7. Response Attribution

AI responses should include citations:
- `[file: filename, page: N]`
- `[file: filename, chunk: N]`
- Clickable links to preview specific chunks

**Security & Privacy:**
- Permission check: User can only cite files in their session
- Audit log: Record citation actions (file_id, user, timestamp)
- No cross-session file access allowed

### 8. Token Budget Control

| Scenario | Strategy |
|----------|----------|
| < 1000 tokens | Inject all relevant chunks |
| 1000-4000 tokens | Top-k retrieval |
| 4000-8000 tokens | Summarize first, then retrieve top-k |
| > 8000 tokens | Error + suggestion to narrow query |
| Many small shallow chunks | Page summary first, then chunk detail |

### 9. Query Reproducibility

- **Stable Scoring**: Use deterministic ranking within score ties
- **Seeded Search**: Allow optional seed for reproducible results
- **Caching**: Cache retrieval results for identical queries within session

## Implementation Plan

### Phase 1: Storage Infrastructure
- [ ] Create `src/hooks/file_context.py`
- [ ] Implement chunk storage (file-based KV)
- [ ] Session metadata management
- [ ] Chunk deduplication logic

### Phase 2: Retrieval System
- [ ] Keyword index (inverted index)
- [ ] Vector embedding support (text-embedding-3-small)
- [ ] Hybrid retrieval (keyword + semantic fusion)
- [ ] Top-k retrieval function
- [ ] Token budget estimator
- [ ] Budget fallback workflow

### Phase 3: AI Integration
- [ ] Modify chat handler to inject file context
- [ ] Implement RAG prompt template
- [ ] Add @file/@all/@last/@chunk command parsing
- [ ] Budget controller with fallback policy
- [ ] Citation rendering

### Phase 4: UI Enhancements
- [ ] File list with parse status in sidebar
- [ ] Quick search / autocomplete for commands
- [ ] "引用这段" button on chunk preview
- [ ] Multi-file context preview
- [ ] Token count indicator
- [ ] Citation auto-scroll to chunk

## API Design

### Parse Job API
```
POST /api/files/parse
  → {job_id: "xxx", status: "pending"}

GET /api/files/parse/{job_id}
  → {job_id, status, progress, result?}
```

### Context Injection API
```
POST /api/context/inject
  {
    session_id,
    message: "What is this about?",
    mode: "auto|explicit",
    top_k: 5,
    max_tokens: 4000,
    include_images: false
  }
  → {chunks: [...], prompt: "...", citations: [...]}
```

### Retrieval API
```
GET /api/chunks/search
  ?session_id=xxx
  &query=revenue
  &top_k=5
  &file_id=xxx (optional filter)
  → {chunks: [...], total: N}
```

### Citation/Audit API
```
POST /api/citations/log
  {
    session_id,
    file_id,
    chunk_id,
    action: "view|reference"
  }
  → {success: true}
```

## File Structure

```
src/
├── hooks/
│   └── file_context.py    # NEW: Chunk storage + retrieval
├── gateway/
│   └── webchat.py         # MOD: Context injection
└── utils/
    └── file_parser/
        └── __init__.py    # MOD: Return structured chunks
```

## Acceptance Criteria

### Functional
- [ ] Upload PDF → auto-parse → ask "What is this document about?" → AI responds
- [ ] Upload multiple files → reference individually with @file_xxx
- [ ] File context persists within session
- [ ] Works with: PDF, Images (OCR), DOCX, XLSX, CSV

### Performance
- [ ] Large files (50+ page PDF / 10k row CSV) don't timeout or OOM
- [ ] Response time < 3s for context injection (excluding LLM)
- [ ] Token budget enforced (no prompt overflow)

### Reliability
- [ ] Same query produces same results (deterministic retrieval)
- [ ] Invalid file references return clear errors
- [ ] Parse failures are logged and user-visible
- [ ] Parse pending → clear warning + retry option
- [ ] Audit log captures citation actions

### Security
- [ ] User can only access their own session files
- [ ] Cross-session file access blocked
- [ ] Citation actions logged for audit

### UX
- [ ] Responses include citations with file/page info
- [ ] Citations are clickable → open chunk preview
- [ ] Parse progress shown for large files
- [ ] Command autocomplete in input
- [ ] Token count indicator visible

## Related Issues

- #269 - File upload support (completed)
- This feature builds on #269
