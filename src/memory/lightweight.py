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

# Schema version for migration support
SCHEMA_VERSION = 2

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
    mtime: Optional[float] = None  # Modification time for auto-refresh


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
    
    def upsert(
        self,
        entry_id: str,
        content: str,
        metadata: Optional[Dict] = None,
        mtime: Optional[float] = None,
    ) -> None:
        """Insert or update a memory entry.
        
        If the entry_id exists, replaces content and metadata.
        If it doesn't exist, creates a new entry.
        
        Args:
            entry_id: Unique entry identifier
            content: Text content
            metadata: Optional metadata
            mtime: Optional modification time (for auto-refresh tracking)
        """
        existing = self.entries.get(entry_id)
        created_at = existing.created_at if existing else datetime.utcnow().isoformat()
        
        entry = MemoryEntry(
            key=entry_id,
            content=content,
            metadata=metadata or {},
            created_at=created_at,
            mtime=mtime,
        )
        self.entries[entry_id] = entry
        self._invalidate_idf_cache()
        self._save_index()
    
    def delete(self, entry_id: str) -> bool:
        """Delete a memory entry.
        
        Args:
            entry_id: Entry identifier to delete
            
        Returns:
            True if entry was deleted, False if not found
        """
        if entry_id in self.entries:
            del self.entries[entry_id]
            self._invalidate_idf_cache()
            self._save_index()
            return True
        return False
    
    def get_entry(self, entry_id: str) -> Optional[Dict[str, Any]]:
        """Get a single entry by ID.
        
        Args:
            entry_id: Entry identifier
            
        Returns:
            Entry dict with id, content, meta, mtime, or None if not found
        """
        entry = self.entries.get(entry_id)
        if not entry:
            return None
        return {
            "id": entry.key,
            "content": entry.content,
            "meta": entry.metadata,
            "mtime": entry.mtime,
            "created_at": entry.created_at,
        }
    
    def delete_by_source(self, source: str) -> int:
        """Delete all entries with matching source in metadata.
        
        Useful for removing all chunks from a removed file.
        
        Args:
            source: Source file path to match
            
        Returns:
            Number of entries deleted
        """
        to_delete = [
            entry_id
            for entry_id, entry in self.entries.items()
            if entry.metadata.get("source") == source
        ]
        for entry_id in to_delete:
            del self.entries[entry_id]
        
        if to_delete:
            self._invalidate_idf_cache()
            self._save_index()
        
        return len(to_delete)
    
    def search(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Search memories using TF-IDF scoring.
        
        Args:
            query: Search query
            limit: Maximum results
            
        Returns:
            List of matching entries with scores, each containing:
            - id: Entry identifier
            - score: Similarity score
            - content: The chunk content (not just preview)
            - meta: Entry metadata
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
                    "id": entry.key,  # New key name
                    "key": entry.key,  # Backward compatibility
                    "score": score,
                    "content": entry.content,  # Return full chunk content
                    "meta": entry.metadata,  # New key name
                    "metadata": entry.metadata,  # Backward compatibility
                })
        
        # Sort by score and limit
        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:limit]
    
    def _load_index(self) -> None:
        """Load index from disk with schema migration support."""
        index_file = self.storage_dir / "index.json"
        if index_file.exists():
            try:
                with open(index_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                # Check for schema version
                version = data.get("version", 1)
                
                if version == 1:
                    # Old schema: {key: content} or {key: {"content": ...}}
                    # Migrate to v2
                    logger.info("Migrating index from v1 to v2")
                    for key, entry_data in data.items():
                        # Handle both string content and dict content
                        if isinstance(entry_data, str):
                            # Old format: {key: "content"}
                            content = entry_data
                            metadata = {}
                        elif isinstance(entry_data, dict):
                            # Intermediate format: {key: {"content": ..., "metadata": ...}}
                            content = entry_data.get("content", "")
                            metadata = entry_data.get("metadata", {})
                        else:
                            continue  # Skip invalid entries
                        
                        self.entries[key] = MemoryEntry(
                            key=key,
                            content=content,
                            metadata=metadata,
                            created_at="",
                            mtime=None,
                        )
                else:
                    # Schema v2: {version, entries: {key: {...}}}
                    entries_data = data.get("entries", data)
                    for key, entry_data in entries_data.items():
                        self.entries[key] = MemoryEntry(
                            key=entry_data.get("key", key),
                            content=entry_data.get("content", ""),
                            metadata=entry_data.get("meta", entry_data.get("metadata", {})),
                            created_at=entry_data.get("created_at", ""),
                            mtime=entry_data.get("mtime"),
                        )
                    
            except Exception as e:
                logger.warning(f"Failed to load index, will rebuild: {e}")
                # If load fails, we'll rebuild from sources
                self.entries.clear()
        
        self._invalidate_idf_cache()
    
    def _save_index(self) -> None:
        """Save index to disk in schema v2 format."""
        index_file = self.storage_dir / "index.json"
        try:
            data = {
                "version": SCHEMA_VERSION,
                "saved_at": datetime.utcnow().isoformat(),
                "entries": {
                    key: {
                        "key": entry.key,
                        "content": entry.content,
                        "meta": entry.metadata,
                        "created_at": entry.created_at,
                        "mtime": entry.mtime,
                    }
                    for key, entry in self.entries.items()
                }
            }
            with open(index_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False)
        except Exception as e:
            logger.debug(f"Failed to save index: {e}")
    
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
