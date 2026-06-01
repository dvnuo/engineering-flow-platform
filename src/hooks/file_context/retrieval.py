"""Retrieval system for file context."""

import re
from collections import defaultdict
from typing import List, Dict, Set, Tuple

from src.config import config, resolve_model_limits, DEFAULT_LLM_MODEL

from .models import Chunk, RetrievalRequest, RetrievalResult
from .storage import storage


def _chunk_search_text(chunk: Chunk) -> str:
    return (chunk.content or chunk.markdown or chunk.table_json or "").strip()


class KeywordIndex:
    """Simple inverted keyword index."""
    
    def __init__(self):
        self.index: Dict[str, Set[str]] = defaultdict(set)  # term -> chunk_ids
        self.chunk_terms: Dict[str, Set[str]] = {}  # chunk_id -> terms
    
    def _tokenize(self, text: str) -> List[str]:
        """Tokenize text into terms."""
        # Simple tokenization: lowercase, alphanumeric
        text = text.lower()
        tokens = re.findall(r'\b[a-z0-9]+\b', text)
        # Filter stopwords
        stopwords = {
            'the', 'a', 'an', 'is', 'are', 'was', 'were',
            'to', 'of', 'in', 'for', 'on', 'at', 'by',
            'and', 'or', 'but', 'not', 'with', 'as',
            'this', 'that', 'from', 'be', 'have', 'has',
            'it', 'its', 'which', 'what', 'how', 'when'
        }
        return [t for t in tokens if t not in stopwords and len(t) > 2]
    
    def add_chunk(self, chunk: Chunk) -> None:
        """Add chunk to index."""
        terms = self._tokenize(_chunk_search_text(chunk))
        self.chunk_terms[chunk.chunk_id] = set(terms)
        
        for term in terms:
            self.index[term].add(chunk.chunk_id)
    
    def search(self, query: str, top_k: int = 10) -> List[Tuple[str, float]]:
        """Search index and return top matching chunk_ids with scores."""
        query_terms = self._tokenize(query)
        
        # Count term matches
        scores: Dict[str, float] = defaultdict(float)
        for term in query_terms:
            if term in self.index:
                for chunk_id in self.index[term]:
                    scores[chunk_id] += 1.0 / len(query_terms)
        
        # Sort by score
        sorted_chunks = sorted(scores.items(), key=lambda x: -x[1])
        return sorted_chunks[:top_k]


class RetrievalEngine:
    """Hybrid retrieval engine for file context."""
    
    def __init__(self):
        self.keyword_index = KeywordIndex()
        self._index_loaded_sessions: Set[str] = set()
    
    def _ensure_index(self, session_id: str) -> None:
        """Load or rebuild index for session."""
        if session_id in self._index_loaded_sessions:
            return
        
        chunks = storage.get_session_chunks(session_id)
        for chunk in chunks:
            self.keyword_index.add_chunk(chunk)
        
        self._index_loaded_sessions.add(session_id)
    
    def rebuild_index(self, session_id: str) -> None:
        """Force rebuild index for session."""
        self.keyword_index = KeywordIndex()
        self._index_loaded_sessions.discard(session_id)
        self._ensure_index(session_id)
    
    def _estimate_tokens(self, chunks: List[Chunk]) -> int:
        """Rough token estimation."""
        # Average token is ~4 characters
        return sum(len(_chunk_search_text(c)) // 4 for c in chunks)
    
    def _filter_images(self, chunks: List[Chunk], include_images: bool) -> List[Chunk]:
        """Filter out image chunks if not requested."""
        if include_images:
            return chunks
        return [c for c in chunks if c.type != 'image']

    def _resolve_budget_thresholds(self, request: RetrievalRequest) -> Tuple[int, int, int]:
        """Resolve retrieval thresholds from model prompt limits, with emergency legacy fallback."""
        llm_cfg = config.llm if isinstance(config.llm, dict) else {}
        model = str(llm_cfg.get("model") or DEFAULT_LLM_MODEL).strip()
        known_model_keys = (
            "gpt-4o",
            "gpt-4.1",
            "gpt-5-mini",
            "gpt-5.3-codex",
            "gpt-5.4",
            "gpt-5.4-mini",
            "gpt-5.5",
            "gemini-2.5-pro",
        )
        model_limits = resolve_model_limits(model or None)
        prompt_budget = int(model_limits.get("max_prompt_tokens") or 0) if any(k in model.lower() for k in known_model_keys) else 0
        if prompt_budget <= 0:
            try:
                prompt_budget = int(request.max_tokens or 0)
            except Exception:
                prompt_budget = 0
        if prompt_budget <= 0:
            # Emergency fallback only if model/request budgets are unavailable.
            return 1000, 4000, 8000
        direct_threshold = max(1000, min(16000, int(prompt_budget * 0.05)))
        topk_threshold = max(direct_threshold + 1, min(64000, int(prompt_budget * 0.20)))
        summarize_threshold = max(topk_threshold + 1, min(128000, int(prompt_budget * 0.50)))
        return direct_threshold, topk_threshold, summarize_threshold
    
    def retrieve(self, request: RetrievalRequest) -> RetrievalResult:
        """Perform retrieval with budget control."""
        self._ensure_index(request.session_id)
        
        # Get candidate chunks
        if request.file_ids:
            all_chunks = []
            for file_id in request.file_ids:
                all_chunks.extend(storage.get_file_chunks(file_id))
        else:
            all_chunks = storage.get_session_completed_files(request.session_id)
            all_chunks_ext = []
            for f in all_chunks:
                all_chunks_ext.extend(storage.get_file_chunks(f.file_id))
            all_chunks = all_chunks_ext
        
        # Filter images if not requested
        all_chunks = self._filter_images(all_chunks, request.include_images)
        all_chunks = [c for c in all_chunks if _chunk_search_text(c)]
        
        if not all_chunks:
            return RetrievalResult(
                chunks=[],
                total_chunks=0,
                estimated_tokens=0,
                budget_status="error",
                citations=[]
            )
        
        # Keyword search
        keyword_results = self.keyword_index.search(request.query, top_k=request.top_k * 2)
        
        # Get top-k chunks
        chunk_ids = [cid for cid, _ in keyword_results[:request.top_k]]
        chunks = []
        for chunk in all_chunks:
            if chunk.chunk_id in chunk_ids:
                chunks.append(chunk)
        
        # Ensure we have chunks (fallback to all if no keyword matches)
        if not chunks:
            chunks = all_chunks[:request.top_k]
        
        # Estimate tokens
        estimated_tokens = self._estimate_tokens(chunks)
        
        # Determine budget status
        direct_threshold, topk_threshold, summarize_threshold = self._resolve_budget_thresholds(request)
        if estimated_tokens < direct_threshold:
            budget_status = "direct"
        elif estimated_tokens < topk_threshold:
            budget_status = "top-k"
        elif estimated_tokens < summarize_threshold:
            budget_status = "summarize"
        else:
            budget_status = "error"
        
        # Build citations
        citations = [
            {
                "chunk_id": c.chunk_id,
                "file_id": c.file_id,
                "page": c.page,
                "type": c.type,
                "preview": (_chunk_search_text(c)[:100] + "...") if len(_chunk_search_text(c)) > 100 else _chunk_search_text(c)
            }
            for c in chunks
        ]
        
        return RetrievalResult(
            chunks=chunks,
            total_chunks=len(chunks),
            estimated_tokens=estimated_tokens,
            budget_status=budget_status,
            citations=citations
        )


# Global instance
retrieval_engine = RetrievalEngine()
