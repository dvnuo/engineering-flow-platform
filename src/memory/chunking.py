"""Chunking utilities for memory system.

Provides chunk_markdown() function to split markdown text into smaller,
semantically coherent chunks based on headings and size limits.
"""

import re
from dataclasses import dataclass, field
from typing import List, Optional
import logging

logger = logging.getLogger(__name__)


@dataclass
class Chunk:
    """A chunk of text from a markdown document.
    
    Attributes:
        id: Stable identifier for the chunk (e.g., "mem:MEMORY.md#h1-01:chunk-01")
        text: The chunk content (trimmed, non-empty)
        meta: Metadata including source, heading, kind, date
    """
    id: str
    text: str
    meta: dict = field(default_factory=dict)
    
    def __repr__(self) -> str:
        return f"Chunk(id={self.id!r}, text={self.text[:50]!r}...)"


def chunk_markdown(
    text: str,
    source_name: str,
    *,
    max_chars: int = 1200,
    min_chars: int = 200,
    kind: str = "core",
    date: Optional[str] = None,
) -> List[Chunk]:
    """Split markdown text into chunks based on headings and size limits.
    
    Args:
        text: The markdown text to chunk
        source_name: Name of the source file (e.g., "MEMORY.md")
        max_chars: Maximum characters per chunk (default 1200)
        min_chars: Minimum characters for a chunk (default 200)
        kind: Type of document ("core" or "daily")
        date: Optional date for daily notes (YYYY-MM-DD format)
    
    Returns:
        List of Chunk objects
    """
    if not text or not text.strip():
        return []
    
    # Normalize line endings
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    
    # Split by headings first (#, ##, ###)
    # Pattern matches #, ##, ### at start of line
    heading_pattern = re.compile(r'^#{1,3}\s+(.+)$', re.MULTILINE)
    
    # Find all heading positions
    headings = []
    for match in heading_pattern.finditer(text):
        headings.append((match.start(), match.group(0), match.group(1).strip()))
    
    # If no headings, treat entire text as one section
    if not headings:
        return _create_chunks_from_text(
            text, source_name, "", max_chars, min_chars, kind, date
        )
    
    # Split text by headings
    sections = []
    for i, (start, heading_markup, heading_text) in enumerate(headings):
        end = headings[i + 1][0] if i + 1 < len(headings) else len(text)
        section_text = text[start:end].strip()
        if section_text:
            sections.append((heading_text, section_text))
    
    # Process each section into chunks
    chunks = []
    for heading_text, section_text in sections:
        section_chunks = _create_chunks_from_text(
            section_text, source_name, heading_text, max_chars, min_chars, kind, date
        )
        chunks.extend(section_chunks)
    
    return chunks


def _create_chunks_from_text(
    text: str,
    source_name: str,
    heading: str,
    max_chars: int,
    min_chars: int,
    kind: str,
    date: Optional[str],
) -> List[Chunk]:
    """Create chunks from a section of text.
    
    If section exceeds max_chars, split by blank lines.
    """
    if not text.strip():
        return []
    
    # Get the full content (heading markup is kept in the text for context)
    content = text.strip()
    
    chunks = []
    
    # If content fits in one chunk
    if len(content) <= max_chars:
        chunk_id = _generate_chunk_id(source_name, heading, len(chunks) + 1, kind)
        meta = {
            "source": source_name,
            "heading": heading,
            "kind": kind,
        }
        if date:
            meta["date"] = date
        
        chunks.append(Chunk(
            id=chunk_id,
            text=content,
            meta=meta,
        ))
        return chunks
    
    # Split by blank lines if section exceeds max_chars
    paragraphs = re.split(r'\n\s*\n', content)
    
    current_chunk_text = ""
    chunk_index = 1
    
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        
        # If single paragraph exceeds max_chars, split it
        while len(para) > max_chars:
            # Take max_chars and try to end at sentence boundary
            chunk_text = para[:max_chars]
            last_period = chunk_text.rfind('. ')
            split_pos = last_period + 1 if last_period > max_chars // 2 else max_chars
            
            if len(chunk_text[:split_pos]) >= min_chars:
                chunk_id = _generate_chunk_id(source_name, heading, chunk_index, kind)
                meta = {
                    "source": source_name,
                    "heading": heading,
                    "kind": kind,
                }
                if date:
                    meta["date"] = date
                
                chunks.append(Chunk(
                    id=chunk_id,
                    text=chunk_text[:split_pos].strip(),
                    meta=meta,
                ))
                chunk_index += 1
            
            para = para[split_pos:]
        
        # If adding this paragraph would exceed max_chars
        if len(current_chunk_text) + len(para) + 1 > max_chars:
            # Save current chunk if it has enough content
            if len(current_chunk_text) >= min_chars:
                chunk_id = _generate_chunk_id(source_name, heading, chunk_index, kind)
                meta = {
                    "source": source_name,
                    "heading": heading,
                    "kind": kind,
                }
                if date:
                    meta["date"] = date
                
                chunks.append(Chunk(
                    id=chunk_id,
                    text=current_chunk_text.strip(),
                    meta=meta,
                ))
                chunk_index += 1
            
            # Start new chunk
            current_chunk_text = para
        else:
            # Add to current chunk
            if current_chunk_text:
                current_chunk_text += "\n\n" + para
            else:
                current_chunk_text = para
    
    # Don't forget the last chunk
    if current_chunk_text.strip() and len(current_chunk_text.strip()) >= min_chars:
        chunk_id = _generate_chunk_id(source_name, heading, chunk_index, kind)
        meta = {
            "source": source_name,
            "heading": heading,
            "kind": kind,
        }
        if date:
            meta["date"] = date
        
        chunks.append(Chunk(
            id=chunk_id,
            text=current_chunk_text.strip(),
            meta=meta,
        ))
    
    return chunks


def _generate_chunk_id(
    source_name: str,
    heading: str,
    chunk_num: int,
    kind: str,
) -> str:
    """Generate a stable chunk ID.
    
    Format:
    - Core: mem:MEMORY.md#heading-slug-01
    - Daily: daily:2026-03-02.md#heading-slug-01
    
    If heading is empty, uses "-" as slug.
    """
    # Create heading slug (alphanumeric, limited length)
    heading_slug = re.sub(r'[^a-zA-Z0-9]', '-', heading.lower())
    heading_slug = re.sub(r'-+', '-', heading_slug).strip('-')
    if not heading_slug:
        heading_slug = "-"
    if len(heading_slug) > 20:
        heading_slug = heading_slug[:20]
    
    if kind == "daily":
        return f"daily:{source_name}#{heading_slug}-{chunk_num:02d}"
    else:
        # For core files, use mem: prefix
        return f"mem:{source_name}#{heading_slug}-{chunk_num:02d}"


def extract_heading(text: str) -> str:
    """Extract the first heading from markdown text.
    
    Args:
        text: Markdown text
    
    Returns:
        The heading text without # marks, or "" if no heading found
    """
    match = re.search(r'^#{1,3}\s+(.+)$', text, re.MULTILINE)
    if match:
        return match.group(1).strip()
    return ""
