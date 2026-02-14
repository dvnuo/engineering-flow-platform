"""Lightweight memory search using keyword matching and FTS5.

This module provides semantic-like search without heavy ML dependencies.
Uses TF-IDF inspired scoring with keyword matching.
"""

import json
import logging
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class MemoryEntry:
    """A memory entry."""
    key: str
    content: str
    metadata: Dict[str, Any]
    created_at: str


class LightweightMemory:
    """Lightweight memory search using keyword matching.
    
    Features:
    - No external ML dependencies
    - TF-IDF inspired scoring
    - Keyword extraction and matching
    - Fast and memory efficient
    """
    
    def __init__(
        self,
        storage_dir: str = "~/.efp/workspace/memory_search",
        score_threshold: float = 0.1,
    ):
        """Initialize lightweight memory.
        
        Args:
            storage_dir: Directory for storage
            score_threshold: Minimum score for results
        """
        self.storage_dir = Path(storage_dir).expanduser()
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.score_threshold = score_threshold
        
        # In-memory index
        self.entries: Dict[str, MemoryEntry] = {}
        
        # Index files
        self._load_index()
    
    def _tokenize(self, text: str) -> List[str]:
        """Tokenize text into words."""
        # Convert to lowercase and extract words
        text = text.lower()
        # Keep alphanumeric + some punctuation
        words = re.findall(r'\b[a-z0-9]+\b', text)
        # Remove very short words
        return [w for w in words if len(w) >= 2]
    
    def _compute_tf(self, tokens: List[str]) -> Dict[str, float]:
        """Compute term frequency."""
        if not tokens:
            return {}
        counter = Counter(tokens)
        total = len(tokens)
        return {word: count / total for word, count in counter.items()}
    
    def _compute_idf(self, documents: List[List[str]]) -> Dict[str, float]:
        """Compute inverse document frequency."""
        import math
        n = len(documents)
        df = Counter()
        for doc in documents:
            unique_words = set(doc)
            for word in unique_words:
                df[word] += 1
        
        idf = {}
        for word, count in df.items():
            idf[word] = math.log(n / count) + 1
        return idf
    
    def _compute_tfidf(self, text: str, idf: Dict[str, float]) -> Dict[str, float]:
        """Compute TF-IDF vector."""
        tokens = self._tokenize(text)
        tf = self._compute_tf(tokens)
        tfidf = {}
        for word, tf_val in tf.items():
            tfidf[word] = tf_val * idf.get(word, 1)
        return tfidf
    
    def _cosine_similarity(self, vec1: Dict[str, float], vec2: Dict[str, float]) -> float:
        """Compute cosine similarity between two TF-IDF vectors."""
        # Get all unique words
        all_words = set(vec1.keys()) | set(vec2.keys())
        if not all_words:
            return 0.0
        
        # Compute dot product and norms
        dot = sum(vec1.get(w, 0) * vec2.get(w, 0) for w in all_words)
        norm1 = sum(v * v for v in vec1.values()) ** 0.5
        norm2 = sum(v * v for v in vec2.values()) ** 0.5
        
        if norm1 == 0 or norm2 == 0:
            return 0.0
        
        return dot / (norm1 * norm2)
    
    def add(self, key: str, content: str, metadata: Optional[Dict] = None) -> None:
        """Add a memory entry.
        
        Args:
            key: Unique key
            content: Text content
            metadata: Optional metadata
        """
        entry = MemoryEntry(
            key=key,
            content=content,
            metadata=metadata or {},
            created_at=datetime.utcnow().isoformat(),
        )
        self.entries[key] = entry
        self._save_index()
    
    def search(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Search memories using TF-IDF scoring.
        
        Args:
            query: Search query
            limit: Maximum results
            
        Returns:
            List of matching entries with scores
        """
        if not self.entries:
            return []
        
        # Tokenize query
        query_tokens = self._tokenize(query)
        if not query_tokens:
            return []
        
        # Compute IDF from all entries
        all_docs = [self._tokenize(e.content) for e in self.entries.values()]
        idf = self._compute_idf(all_docs)
        
        # Compute query TF-IDF
        query_tfidf = self._compute_tfidf(query, idf)
        
        # Compute similarity for each entry
        results = []
        for entry in self.entries.values():
            entry_tfidf = self._compute_tfidf(entry.content, idf)
            score = self._cosine_similarity(query_tfidf, entry_tfidf)
            
            if score >= self.score_threshold:
                results.append({
                    "key": entry.key,
                    "content": entry.content[:200],  # Truncate for display
                    "score": score,
                    "metadata": entry.metadata,
                })
        
        # Sort by score and limit
        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:limit]
    
    def _load_index(self) -> None:
        """Load index from disk."""
        index_file = self.storage_dir / "index.json"
        if index_file.exists():
            try:
                with open(index_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    for key, entry_data in data.items():
                        self.entries[key] = MemoryEntry(
                            key=entry_data["key"],
                            content=entry_data["content"],
                            metadata=entry_data.get("metadata", {}),
                            created_at=entry_data.get("created_at", ""),
                        )
            except Exception as e:
                logger.debug(f"Failed to load index: {e}")
    
    def _save_index(self) -> None:
        """Save index to disk."""
        index_file = self.storage_dir / "index.json"
        try:
            data = {
                key: {
                    "key": entry.key,
                    "content": entry.content,
                    "metadata": entry.metadata,
                    "created_at": entry.created_at,
                }
                for key, entry in self.entries.items()
            }
            with open(index_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False)
        except Exception as e:
            logger.debug(f"Failed to save index: {e}")
    
    def delete(self, key: str) -> bool:
        """Delete an entry."""
        if key in self.entries:
            del self.entries[key]
            self._save_index()
            return True
        return False
    
    def clear(self) -> None:
        """Clear all entries."""
        self.entries.clear()
        self._save_index()
    
    def count(self) -> int:
        """Get entry count."""
        return len(self.entries)
    
    def health_check(self) -> Dict[str, Any]:
        """Get health status."""
        return {
            "type": "lightweight",
            "storage_dir": str(self.storage_dir),
            "entry_count": self.count(),
        }
