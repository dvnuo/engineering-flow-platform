# File AI Integration - Requirements

## Overview

Enable users to chat with AI about uploaded files. The AI should be able to understand and discuss the content of uploaded files (images, PDFs, documents, etc.).

## Current State

### Working
- File upload via web UI
- File parsing (OCR, PDF text extraction, document parsing)
- File listing and management

### Not Working
- AI cannot access or discuss uploaded file content
- No context integration between file parsing and chat

## Requirements

### 1. File Context Storage
- Store parsed content per session
- Structure:
  ```json
  {
    "session_id": "xxx",
    "files": [
      {
        "file_id": "xxx",
        "filename": "document.pdf",
        "content_type": "application/pdf",
        "parsed": true,
        "content": "Extracted text content...",
        "blocks": [...],
        "uploaded_at": "2026-03-01T12:00:00Z"
      }
    ]
  }
  ```

### 2. Auto-parse on Upload
- After successful upload, automatically trigger parse
- Store parsed result in session context

### 3. AI Context Injection
- When user sends a message, check for related files in session
- Inject file content into AI prompt
- Option A: Include relevant content in system prompt
- Option B: Use RAG-style retrieval

### 4. File Reference Commands
- `@file_xxx` - Reference specific file
- `@last` - Reference last uploaded file
- `@all` - Include all uploaded files in context

### 5. UI Enhancements
- Show uploaded files in sidebar
- Quick actions: reference, delete, re-parse
- File content preview on hover/click

## Implementation Plan

### Phase 1: Context Storage
- Create `src/hooks/file_context.py`
- Store parsed content in session
- API to retrieve file content

### Phase 2: Auto-parse Integration
- Modify upload handler to auto-parse
- Store result in session context

### Phase 3: AI Integration
- Modify chat handler to include file context
- Test with various file types

### Phase 4: UI Polish
- File list in sidebar
- Reference commands

## File Structure

```
src/
├── hooks/
│   └── file_context.py    # NEW: File context storage
├── gateway/
│   └── webchat.py        # MOD: Auto-parse, context injection
└── utils/
    └── file_parser/
        └── __init__.py   # MOD: Return structured content
```

## Acceptance Criteria

1. User can upload a PDF and ask "What is this document about?"
2. AI responds with summary of the document content
3. User can upload multiple files and reference them individually
4. File context persists within session
5. Works with: PDF, Images (OCR), DOCX, XLSX, CSV

## Related Issues

- #269 - File upload feature (completed)
- This feature builds on top of #269
