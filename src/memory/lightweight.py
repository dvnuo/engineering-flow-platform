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

# Common English stop words (filtered from search results)
STOP_WORDS = {
    'i', 'me', 'my', 'myself', 'we', 'our', 'ours', 'ourselves', 'you', "you're",
    "you've", "you'll", "you'd", 'your', 'yours', 'yourself', 'yourselves', 'he',
    'him', 'his', 'himself', 'she', "she's", 'her', 'hers', 'herself', 'it', "it's",
    'its', 'itself', 'they', 'them', 'their', 'theirs', 'themselves', 'what', 'which',
    'who', 'whom', 'this', 'that', "that'll", 'these', 'those', 'am', 'is', 'are',
    'was', 'were', 'be', 'been', 'being', 'have', 'has', 'had', 'having', 'do',
    'does', 'did', 'doing', 'a', 'an', 'the', 'and', 'but', 'if', 'or', 'because',
    'as', 'until', 'while', 'of', 'at', 'by', 'for', 'with', 'about', 'against',
    'between', 'into', 'through', 'during', 'before', 'after', 'above', 'below',
    'to', 'from', 'up', 'down', 'in', 'out', 'on', 'off', 'over', 'under', 'again',
    'further', 'then', 'once', 'here', 'there', 'when', 'where', 'why', 'how', 'all',
    'each', 'few', 'more', 'most', 'other', 'some', 'such', 'no', 'nor', 'not',
    'only', 'own', 'same', 'so', 'than', 'too', 'very', 's', 't', 'can', 'will',
    'just', 'don', "don't", 'should', "should've", 'now', 'd', 'll', 'm', 'o',
    're', 've', 'y', 'ain', 'aren', "aren't", 'couldn', "couldn't", 'didn', "didn't",
    'doesn', "doesn't", 'hadn', "hadn't", 'hasn', "hasn't", 'haven', "haven't",
    'isn', "isn't", 'ma', 'mightn', "mightn't", 'mustn', "mustn't", 'needn',
    "needn't", 'shan', "shan't", 'shouldn', "shouldn't", 'wasn', "wasn't", 'weren',
    "weren't", 'won', "won't", 'wouldn', "wouldn't"
}


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
    - TF-IDF inspired scoring with stop word filtering
    - IDF caching for performance
    - Keyword extraction and matching
    - Fast and memory efficient
    """
    
    # Default content preview length
    DEFAULT_PREVIEW_LENGTH = 500
    
    def __init__(
        self,
        storage_dir: str = "~/.efp/workspace/memory_search",
        score_threshold: float = 0.1,
        preview_length: int = DEFAULT_PREVIEW_LENGTH,
    ):
        """Initialize lightweight memory.
        
        Args:
            storage_dir: Directory for storage
            score_threshold: Minimum score for results
            preview_length: Length of content preview in search results
        """
        self.storage_dir = Path(storage_dir).expanduser()
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.score_threshold = score_threshold
        self.preview_length = preview_length
        
        # In-memory index
        self.entries: Dict[str, MemoryEntry] = {}
        
        # Cached IDF (invalidated on add/delete)
        self._idf_cache: Optional[Dict[str, float]] = None
        
        # Index files
        self._load_index()
    
    def _tokenize(self, text: str) -> List[str]:
        """Tokenize text into words.
        
        Args:
            text: Input text
            
        Returns:
            List of tokens (lowercase, filtered)
        """
        # Convert to lowercase and extract words (includes underscore)
        text = text.lower()
        words = re.findall(r'\b\w+\b', text)
        # Remove stop words and very short words
        return [w for w in words if len(w) >= 2 and w not in STOP_WORDS]
    
    def _compute_tf(self, tokens: List[str]) -> Dict[str, float]:
        """Compute term frequency.
        
        Args:
            tokens: List of tokens
            
        Returns:
            TF dictionary {word: frequency}
        """
        if not tokens:
            return {}
        counter = Counter(tokens)
        total = len(tokens)
        return {word: count / total for word, count in counter.items()}
    
    def _compute_idf(self, documents: List[List[str]]) -> Dict[str, float]:
        """Compute inverse document frequency.
        
        Args:
            documents: List of tokenized documents
            
        Returns:
            IDF dictionary {word: idf_score}
        """
        import math
        n = len(documents)
        if n == 0:
            return {}
        
        df = Counter()
        for doc in documents:
            unique_words = set(doc)
            for word in unique_words:
                df[word] += 1
        
        idf = {}
        for word, count in df.items():
            idf[word] = math.log(n / count) + 1
        return idf
    
    def _invalidate_idf_cache(self) -> None:
        """Invalidate IDF cache."""
        self._idf_cache = None
    
    def _get_idf(self) -> Dict[str, float]:
        """Get IDF cache or compute it.
        
        Returns:
            IDF dictionary
        """
        if self._idf_cache is None:
            all_docs = [self._tokenize(e.content) for e in self.entries.values()]
            self._idf_cache = self._compute_idf(all_docs)
        return self._idf_cache
    
    def _compute_tfidf(self, text: str, idf: Dict[str, float]) -> Dict[str, float]:
        """Compute TF-IDF vector.
        
        Args:
            text: Input text
            idf: IDF dictionary
            
        Returns:
            TF-IDF vector dictionary
        """
        tokens = self._tokenize(text)
        tf = self._compute_tf(tokens)
        tfidf = {}
        for word, tf_val in tf.items():
            tfidf[word] = tf_val * idf.get(word, 1)
        return tfidf
    
    def _cosine_similarity(self, vec1: Dict[str, float], vec2: Dict[str, float]) -> float:
        """Compute cosine similarity between two TF-IDF vectors.
        
        Args:
            vec1: First vector
            vec2: Second vector
            
        Returns:
            Cosine similarity score (0-1)
        """
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
        self._invalidate_idf_cache()
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
        
        # Get cached IDF
        idf = self._get_idf()
        
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
                    "content": entry.content[:self.preview_length],  # Configurable preview
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
        self._invalidate_idf_cache()
    
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
        """Delete an entry.
        
        Args:
            key: Key to delete
            
        Returns:
            True if deleted, False if not found
        """
        if key in self.entries:
            del self.entries[key]
            self._invalidate_idf_cache()
            self._save_index()
            return True
        return False
    
    def clear(self) -> None:
        """Clear all entries."""
        self.entries.clear()
        self._invalidate_idf_cache()
        self._save_index()
    
    def count(self) -> int:
        """Get total entry count.
        
        Returns:
            Number of entries
        """
        return len(self.entries)
    
    def health_check(self) -> Dict[str, Any]:
        """Check health status.
        
        Returns:
            Health status dictionary
        """
        return {
            "type": "lightweight",
            "storage_dir": str(self.storage_dir),
            "entry_count": self.count(),
            "idf_cache_valid": self._idf_cache is not None,
        }
