"""Vector memory with Qdrant backend for semantic search.

Provides semantic search capability for workspace memory files.
"""

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class MemoryEntry:
    """A memory entry with vector embedding."""
    key: str
    content: str
    vector: List[float]
    metadata: Dict[str, Any]
    created_at: str


class VectorMemory:
    """Qdrant-based vector memory with semantic search.
    
    Falls back to numpy-based storage if Qdrant is unavailable.
    """
    
    def __init__(
        self,
        collection_name: str = "efp_memory",
        storage_dir: str = "~/.efp/workspace/vector_memory",
        embedding_model: str = "all-MiniLM-L6-v2",
        dimension: int = 384,
        score_threshold: float = 0.5,
    ):
        """Initialize vector memory.
        
        Args:
            collection_name: Qdrant collection name
            storage_dir: Directory for persistent storage
            embedding_model: Sentence transformer model name
            dimension: Embedding dimension
            score_threshold: Minimum similarity score for search results
        """
        self.collection_name = collection_name
        self.storage_dir = Path(storage_dir).expanduser()
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.embedding_model = embedding_model
        self.dimension = dimension
        self.score_threshold = score_threshold
        
        self.qdrant = None
        self.has_qdrant = False
        self._init_qdrant()
        
        if not self.has_qdrant:
            self._init_fallback()
    
    def _init_qdrant(self):
        """Initialize Qdrant client."""
        try:
            from qdrant_client import QdrantClient
            from qdrant_client.models import VectorParams, Distance
            
            # Try to use local Qdrant first
            qdrant_path = self.storage_dir / "qdrant"
            self.qdrant = QdrantClient(path=str(qdrant_path))
            
            # Try to get collection, create if not exists
            try:
                self.qdrant.get_collection(self.collection_name)
            except Exception:
                self.qdrant.create_collection(
                    collection_name=self.collection_name,
                    vectors_config=VectorParams(size=self.dimension, distance=Distance.COSINE),
                )
            
            self.has_qdrant = True
            logger.info(f"Qdrant initialized successfully at {qdrant_path}")
            
        except ImportError:
            logger.debug("qdrant-client not installed, using fallback")
            self.has_qdrant = False
        except Exception as e:
            logger.debug(f"Failed to initialize Qdrant: {e}, using fallback")
            self.has_qdrant = False
    
    def _init_fallback(self):
        """Initialize numpy-based fallback storage."""
        self.vectors_file = self.storage_dir / "vectors.npz"
        self.metadata_file = self.storage_dir / "metadata.jsonl"
        
        # Load existing data
        if self.vectors_file.exists():
            self.fallback_vectors = np.load(str(self.vectors_file))
        else:
            self.fallback_vectors = np.array([]).reshape(0, self.dimension)
        
        self.fallback_metadata = self._load_metadata()
        logger.info(f"Fallback vector storage initialized at {self.storage_dir}")
    
    def _load_metadata(self) -> List[Dict[str, Any]]:
        """Load metadata from file."""
        if self.metadata_file.exists():
            metadata = []
            with open(self.metadata_file, 'r', encoding='utf-8') as f:
                for line in f:
                    try:
                        metadata.append(json.loads(line.strip()))
                    except json.JSONDecodeError:
                        pass
            return metadata
        return []
    
    def _get_embedding(self, text: str) -> List[float]:
        """Get embedding for text.
        
        Args:
            text: Text to embed
            
        Returns:
            Embedding vector as list of floats
        """
        try:
            from sentence_transformers import SentenceTransformer
            model = SentenceTransformer(self.embedding_model)
            return model.encode(text).tolist()
        except ImportError:
            logger.debug("sentence-transformers not installed, using simple embedding")
            return self._simple_embedding(text)
        except Exception as e:
            logger.debug(f"Failed to generate embedding: {e}, using simple embedding")
            return self._simple_embedding(text)
    
    def _simple_embedding(self, text: str) -> List[float]:
        """Generate a simple deterministic embedding from text hash.
        
        Args:
            text: Input text
            
        Returns:
            Deterministic embedding vector
        """
        import hashlib
        
        hash_bytes = hashlib.sha256(text.encode()).digest()
        # Generate 48 bytes (384 bits) from hash
        vector = [float(b) / 255.0 for b in hash_bytes[:48]]
        
        # Pad or truncate to dimension
        while len(vector) < self.dimension:
            vector.extend(vector[:self.dimension - len(vector)])
        vector = vector[:self.dimension]
        
        return vector
    
    def add(
        self,
        key: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Add a memory entry.
        
        Args:
            key: Unique key for the memory
            content: Text content to embed and store
            metadata: Optional metadata dictionary
        """
        vector = self._get_embedding(content)
        entry = MemoryEntry(
            key=key,
            content=content,
            vector=vector,
            metadata=metadata or {},
            created_at=datetime.utcnow().isoformat(),
        )
        
        if self.has_qdrant and self.qdrant:
            try:
                from qdrant_client.models import PointStruct
                
                # Use hash of key as point ID
                import hashlib
                point_id = int(hashlib.md5(key.encode()).hexdigest()[:8], 16)
                
                self.qdrant.upsert_points(
                    collection_name=self.collection_name,
                    points=[PointStruct(
                        id=point_id,
                        vector=vector,
                        payload={
                            "key": key,
                            "content": content,
                            "metadata": entry.metadata,
                            "created_at": entry.created_at,
                        }
                    )]
                )
            except Exception as e:
                logger.error(f"Failed to add to Qdrant: {e}")
        
        if not self.has_qdrant:
            # Add to fallback storage
            if len(self.fallback_vectors) == 0:
                self.fallback_vectors = np.array([vector])
            else:
                self.fallback_vectors = np.vstack([self.fallback_vectors, vector])
            
            self.fallback_metadata.append({
                "key": key,
                "content": content,
                "metadata": entry.metadata,
                "created_at": entry.created_at,
            })
            self._save_fallback()
    
    def search(
        self,
        query: str,
        limit: int = 5,
        score_threshold: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        """Search memories by semantic similarity.
        
        Args:
            query: Search query
            limit: Maximum number of results
            score_threshold: Minimum similarity score (default: instance threshold)
            
        Returns:
            List of matching entries with score
        """
        threshold = score_threshold if score_threshold is not None else self.score_threshold
        
        try:
            query_vector = self._get_embedding(query)
            return self._search_vector(query_vector, limit, threshold)
        except Exception as e:
            logger.error(f"Search failed: {e}")
            return []
    
    def _search_vector(
        self,
        query_vector: List[float],
        limit: int,
        threshold: float,
    ) -> List[Dict[str, Any]]:
        """Perform vector search.
        
        Args:
            query_vector: Query embedding
            limit: Maximum results
            threshold: Minimum score
            
        Returns:
            Search results
        """
        if self.has_qdrant and self.qdrant:
            try:
                results = self.qdrant.search(
                    collection_name=self.collection_name,
                    query_vector=query_vector,
                    limit=limit,
                    score_threshold=threshold,
                )
                return [
                    {
                        "key": r.payload.get("key", ""),
                        "content": r.payload.get("content", ""),
                        "score": float(r.score),
                        "metadata": r.payload.get("metadata", {}),
                    }
                    for r in results
                ]
            except Exception as e:
                logger.error(f"Qdrant search failed: {e}")
        
        # Fallback: numpy-based search
        return self._fallback_search(query_vector, limit, threshold)
    
    def _fallback_search(
        self,
        query_vector: List[float],
        limit: int,
        threshold: float,
    ) -> List[Dict[str, Any]]:
        """Perform fallback numpy-based search.
        
        Args:
            query_vector: Query embedding
            limit: Maximum results
            threshold: Minimum score
            
        Returns:
            Search results
        """
        if len(self.fallback_vectors) == 0:
            return []
        
        query = np.array(query_vector)
        vectors = self.fallback_vectors
        
        # Calculate cosine similarity
        norms = np.linalg.norm(vectors, axis=1) * np.linalg.norm(query)
        if np.any(norms == 0):
            return []
        
        similarities = np.dot(vectors, query) / norms
        
        # Get top results above threshold
        top_indices = np.argsort(similarities)[-limit:][::-1]
        
        results = []
        for idx in top_indices:
            score = float(similarities[idx])
            if score >= threshold:
                meta = self.fallback_metadata[idx]
                results.append({
                    "key": meta.get("key", ""),
                    "content": meta.get("content", ""),
                    "score": score,
                    "metadata": meta.get("metadata", {}),
                })
        
        return results
    
    def _save_fallback(self) -> None:
        """Save fallback storage to disk."""
        try:
            np.save(str(self.vectors_file), self.fallback_vectors)
            
            with open(self.metadata_file, 'w', encoding='utf-8') as f:
                for meta in self.fallback_metadata:
                    f.write(json.dumps(meta) + "\n")
        except Exception as e:
            logger.error(f"Failed to save fallback storage: {e}")
    
    def delete(self, key: str) -> bool:
        """Delete a memory entry by key.
        
        Args:
            key: Key to delete
            
        Returns:
            True if deleted, False if not found
        """
        if self.has_qdrant and self.qdrant:
            try:
                import hashlib
                point_id = int(hashlib.md5(key.encode()).hexdigest()[:8], 16)
                self.qdrant.delete_points(
                    collection_name=self.collection_name,
                    points=[point_id],
                )
                return True
            except Exception:
                pass
        
        # Fallback: remove from metadata
        for i, meta in enumerate(self.fallback_metadata):
            if meta.get("key") == key:
                self.fallback_metadata.pop(i)
                self.fallback_vectors = np.delete(self.fallback_vectors, i, axis=0)
                self._save_fallback()
                return True
        
        return False
    
    def clear(self) -> None:
        """Clear all memories."""
        if self.has_qdrant and self.qdrant:
            try:
                self.qdrant.delete_collection(self.collection_name)
                self.qdrant.create_collection(
                    collection_name=self.collection_name,
                    vectors_config=self.qdrant.models.VectorParams(
                        size=self.dimension,
                        distance=self.qdrant.models.Distance.COSINE,
                    ),
                )
            except Exception as e:
                logger.error(f"Failed to clear Qdrant collection: {e}")
        
        # Clear fallback
        self.fallback_vectors = np.array([]).reshape(0, self.dimension)
        self.fallback_metadata = []
        self._save_fallback()
    
    def count(self) -> int:
        """Get total number of stored memories.
        
        Returns:
            Number of entries
        """
        if self.has_qdrant and self.qdrant:
            try:
                return self.qdrant.get_collection(self.collection_name).points_count
            except Exception:
                pass
        
        return len(self.fallback_metadata)
    
    def health_check(self) -> Dict[str, Any]:
        """Check health status.
        
        Returns:
            Health status dictionary
        """
        return {
            "qdrant_available": self.has_qdrant,
            "storage_dir": str(self.storage_dir),
            "collection_name": self.collection_name,
            "dimension": self.dimension,
            "embedding_model": self.embedding_model,
            "entry_count": self.count(),
        }
