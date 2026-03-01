# File AI Integration - Requirements

## Overview

Enable users to chat with AI about uploaded files. The AI should be understand and discuss the content of uploaded files (images, PDFs, documents, etc.).

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
      "parse_status": "completed|pending|failed",
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
  "extracted_at": "2026-03-01T12:00:00Z"
}
```

### 2. Auto-parse with Async Job Support

- Upload triggers parse job (large files: async with job_id)
- Parse status: `pending` → `processing` → `completed|failed`
- Build retrieval index after parse (keyword minimum, embedding preferred)

### 3. AI Context Injection (Default: RAG)

**Default Behavior** (RAG-style):
- User message → retrieve top-k relevant chunks → inject into prompt
- Budget controls:
  - `top_k`: 5-12 chunks
  - `max_chunk_size`: 800-1500 tokens
  - `max_total_tokens`: 4000-6000 for file context
- Fallback when over budget: summarize first, then retrieve

**Explicit Reference** (`@file_xxx`, `@all`):
- Bypasses RAG, retrieves all chunks from specified file(s)
- Only when file is small enough or user explicitly requests

### 4. File Reference Commands

**Syntax Rules:**
- `@file_xxx` - Reference specific file by ID
- `@last` - Reference last uploaded file
- `@all` - Include all files in session
- `@chunk_xxx` - Reference specific chunk (from UI selection)

**Priority & Combination:**
- Multiple refs: union of all referenced files
- `@all` + specific: specific files override `@all`
- Invalid reference: return error with available files list

**Error Handling:**
- File not found: `{error: "File not found", available_files: [...]}`
- Empty result: `{warning: "No relevant content found", suggestions: [...]}`

### 5. Token Budget Control

| Scenario | Strategy |
|----------|----------|
| < 1000 tokens | Inject all relevant chunks |
| 1000-4000 tokens | Top-k retrieval |
| > 4000 tokens | Summarize first, then retrieve top-k |
| > 8000 tokens | Error + suggestion to narrow query |

### 6. Response Attribution

AI responses should include citations:
- `[file: filename, page: N]` 
- `[file: filename, chunk: N]`
- Clickable links to preview specific chunks

## Implementation Plan

### Phase 1: Storage Infrastructure
- [ ] Create `src/hooks/file_context.py`
- [ ] Implement chunk storage (file-based KV)
- [ ] Session metadata management

### Phase 2: Retrieval System
- [ ] Simple keyword index (inverted index on content)
- [ ] Top-k retrieval function
- [ ] Token budget estimator

### Phase 3: AI Integration
- [ ] Modify chat handler to inject file context
- [ ] Implement RAG prompt template
- [ ] Add @file/@all/@last command parsing

### Phase 4: UI Enhancements
- [ ] File list with parse status in sidebar
- [ ] "引用这段" button on chunk preview
- [ ] Citation rendering in chat

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
    mode: "auto|explicit",  // auto = RAG, explicit = @file refs
    top_k: 5,
    max_tokens: 4000
  }
  → {chunks: [...], prompt: "..."}
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

### UX
- [ ] Responses include citations with file/page info
- [ ] Citations are clickable → open chunk preview
- [ ] Parse progress shown for large files

## Related Issues

- #269 - File upload support (completed)
- This feature builds on #269
