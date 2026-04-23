"""Context injection for AI prompts."""

from typing import Tuple, List, Optional

from .models import Chunk, RetrievalResult
from .retrieval import retrieval_engine
from .parser import CommandParser
from .storage import storage


def build_rag_prompt(
    user_message: str,
    retrieval_result: RetrievalResult
) -> Tuple[str, str]:
    """Build prompt with retrieved context.
    
    Returns:
        (prompt, budget_status)
    """
    budget_status = retrieval_result.budget_status
    
    if budget_status == "error":
        return "", "error:query_too_broad"
    
    chunks = retrieval_result.chunks
    
    if not chunks:
        return user_message, "no_context"
    
    # Build context from chunks
    context_parts = []
    for i, chunk in enumerate(chunks, 1):
        source_info = f"[{chunk.file_id}"
        if chunk.page:
            source_info += f", page {chunk.page}"
        source_info += "]"
        
        context_parts.append(f"--- Context {i} {source_info} ---\n{chunk.content}")
    
    context_text = "\n\n".join(context_parts)
    
    # Build prompt based on budget status
    if budget_status == "direct":
        prompt = f"""Based on the following context, answer the user's question.

Context:
{context_text}

Question: {user_message}

Answer:"""
    
    elif budget_status == "top-k":
        prompt = f"""Based on the following context (top-k relevant excerpts), answer the user's question.

Context:
{context_text}

Question: {user_message}

Answer:"""
    
    elif budget_status == "summarize":
        prompt = f"""The relevant context is too large. First summarize the context briefly, then answer the question.

Context:
{context_text}

Question: {user_message}

First, provide a brief summary of the relevant context, then answer:"""
    
    else:
        prompt = f"""Question: {user_message}

Answer:"""
    
    return prompt, budget_status


def format_citations(chunks: List[Chunk]) -> str:
    """Format citations for display."""
    if not chunks:
        return ""
    
    # Group by file
    file_citations = {}
    for chunk in chunks:
        if chunk.file_id not in file_citations:
            file_citations[chunk.file_id] = []
        file_citations[chunk.file_id].append(chunk.page)
    
    citations = []
    for file_id, pages in file_citations.items():
        meta = storage.get_file_meta(chunks[0].session_id, file_id)
        filename = meta.filename if meta else file_id
        
        if pages:
            unique_pages = sorted(set(p for p in pages if p))
            if len(unique_pages) == 1:
                citations.append(f"[{filename}, page {unique_pages[0]}]")
            else:
                citations.append(f"[{filename}, pages {', '.join(map(str, unique_pages))}]")
        else:
            citations.append(f"[{filename}]")
    
    return "Sources: " + ", ".join(citations)


def inject_context(
    session_id: str,
    message: str,
    top_k: int = 5,
    max_tokens: int = 4000,
    include_images: bool = False,
    preferred_file_ids: Optional[List[str]] = None,
) -> Tuple[str, str, List[dict]]:
    """Inject file context into user message.
    
    Returns:
        (enhanced_message, budget_status, citations)
    """
    # Parse any file reference commands
    cleaned_message, references = CommandParser.parse(message)
    
    # Get session files
    session_files = storage.get_session_files(session_id)
    
    # Resolve references to file IDs
    file_ids = CommandParser.resolve_references(references, session_files)
    effective_file_ids = file_ids if file_ids else (preferred_file_ids or None)
    
    # Check for explicit chunk references
    chunk_ids = CommandParser.extract_chunk_refs(references)
    
    # If explicit chunk refs, retrieve those directly
    if chunk_ids:
        chunks = []
        for chunk_id in chunk_ids:
            # Need to find the file_id for this chunk
            for f in session_files:
                chunk = storage.get_chunk(f.file_id, chunk_id)
                if chunk:
                    chunks.append(chunk)
                    break
        # Build result manually
        estimated_tokens = sum(len(c.content) // 4 for c in chunks)
        retrieval_result = RetrievalResult(
            chunks=chunks,
            total_chunks=len(chunks),
            estimated_tokens=estimated_tokens,
            budget_status="direct" if estimated_tokens < 4000 else "top-k",
            citations=[]
        )
    else:
        # Use retrieval engine
        from .models import RetrievalRequest
        retrieval_request = RetrievalRequest(
            session_id=session_id,
            query=cleaned_message,
            top_k=top_k,
            max_tokens=max_tokens,
            file_ids=effective_file_ids,
            include_images=include_images
        )
        retrieval_result = retrieval_engine.retrieve(retrieval_request)
    
    # Build prompt
    prompt, budget_status = build_rag_prompt(cleaned_message, retrieval_result)
    
    # Format citations
    citations = retrieval_result.citations
    
    return prompt, budget_status, citations
