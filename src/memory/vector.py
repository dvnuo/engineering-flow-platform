"""Vector memory with embedding backend for semantic search.

Uses transformers for embeddings with local NumPy storage.
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
    """Vector memory with transformer embeddings for semantic search.
    
    Uses transformers library for embeddings.
    Stores embeddings in local NumPy format.
    """
    
    def __init__(
        self,
        collection_name: str = "efp_memory",
        storage_dir: str = "~/.efp/workspace/vector_memory",
        embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2",
        dimension: int = 384,
        score_threshold: float = 0.5,
        max_tokens: int = 256,
    ):
        """Initialize vector memory.
        
        Args:
            collection_name: Storage collection name
            storage_dir: Directory for persistent storage
            embedding_model: HuggingFace model name
            dimension: Embedding dimension
            score_threshold: Minimum similarity score for search results
            max_tokens: Maximum tokens for embedding input
        """
        self.collection_name = collection_name
        self.storage_dir = Path(storage_dir).expanduser()
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.embedding_model = embedding_model
        self.dimension = dimension
        self.score_threshold = score_threshold
        self.max_tokens = max_tokens
        
        self.model = None
        self.tokenizer = None
        self._init_model()
        
        # Initialize numpy-based storage
        self._init_storage()
    
    def _init_model(self):
        """Initialize transformer model for embeddings."""
        self.has_model = False
        
        try:
            from transformers import AutoTokenizer, AutoModel
            import torch
            
            cache_dir = self.storage_dir / "models"
            cache_dir.mkdir(parents=True, exist_ok=True)
            
            logger.info(f"Loading embedding model: {self.embedding_model}")
            
            self.tokenizer = AutoTokenizer.from_pretrained(
                self.embedding_model,
                cache_dir=str(cache_dir)
            )
            self.model = AutoModel.from_pretrained(
                self.embedding_model,
                cache_dir=str(cache_dir)
            )
            self.model.eval()
            
            self.has_model = True
            logger.info(f"Embedding model loaded: {self.embedding_model}")
            
        except ImportError:
            logger.debug("transformers not installed")
        except Exception as e:
            logger.debug(f"Failed to load model: {e}")
        
        if not self.has_model:
            logger.info("Using fallback hash-based embeddings")
    
    def _get_embedding(self, text: str) -> List[float]:
        """Get embedding for text using transformer model.
        
        Args:
            text: Text to embed
            
        Returns:
            Embedding vector as list of floats
        """
        if self.has_model and self.model:
            try:
                import torch
                
                # Tokenize
                inputs = self.tokenizer(
                    text,
                    padding=True,
                    truncation=True,
                    max_length=self.max_tokens,
                    return_tensors="pt"
                )
                
                # Get embeddings
                with torch.no_grad():
                    outputs = self.model(**inputs)
                    # Mean pooling
                    attention_mask = inputs["attention_mask"]
                    token_embeddings = outputs.last_hidden_state
                    input_mask_expanded = attention_mask.unsqueeze(-1).expand(token_embeddings.size()).float()
                    sum_embeddings = torch.sum(token_embeddings * input_mask_expanded, 1)
                    sum_mask = torch.clamp(input_mask_expanded.sum(1), min=1e-9)
                    embedding = (sum_embeddings / sum_mask).squeeze()
                    
                    # Normalize
                    embedding = torch.nn.functional.normalize(embedding, p=2, dim=0)
                    
                    return embedding.numpy().tolist()
                    
            except Exception as e:
                logger.debug(f"Embedding failed: {e}")
        
        return self._simple_embedding(text)
    
    def _simple_embedding(self, text: str) -> List[float]:
        """Generate a simple deterministic embedding from text hash.
        
        Args:
            text: Input text
            
        Returns:
            Deterministic embedding vector (non-semantic, for fallback)
        """
        import hashlib
        
        hash_bytes = hashlib.sha256(text.encode()).digest()
        # Generate 384 bits (48 bytes) from hash
        vector = [float(b) / 255.0 for b in hash_bytes[:48]]
        
        # Pad or truncate to dimension
        while len(vector) < self.dimension:
            vector.extend(vector[:self.dimension - len(vector)])
        vector = vector[:self.dimension]
        
        return vector
    
    def _init_storage(self):
        """Initialize numpy-based storage."""
        self.vectors_file = self.storage_dir / "vectors.npz"
        self.metadata_file = self.storage_dir / "metadata.jsonl"
        
        # Load existing data
        if self.vectors_file.exists():
            self.fallback_vectors = np.load(str(self.vectors_file))
        else:
            self.fallback_vectors = np.array([]).reshape(0, self.dimension)
        
        self.fallback_metadata = self._load_metadata()
        logger.info(f"Vector storage initialized at {self.storage_dir}")
    
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
        
        # Add to numpy storage
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
        self._save_storage()
    
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
            return self._search_vectors(query_vector, limit, threshold)
        except Exception as e:
            logger.error(f"Search failed: {e}")
            return []
    
    def _search_vectors(
        self,
        query_vector: List[float],
        limit: int,
        threshold: float,
    ) -> List[Dict[str, Any]]:
        """Perform vector search using cosine similarity.
        
        Args:
            query_vector: Query embedding
            limit: Maximum results
            threshold: Minimum score
            
        Returns:
            Search results sorted by score
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
    
    def _save_storage(self):
        """Save storage to disk."""
        try:
            np.save(str(self.vectors_file), self.fallback_vectors)
            
            with open(self.metadata_file, 'w', encoding='utf-8') as f:
                for meta in self.fallback_metadata:
                    f.write(json.dumps(meta) + "\n")
        except Exception as e:
            logger.error(f"Failed to save storage: {e}")
    
    def delete(self, key: str) -> bool:
        """Delete a memory entry by key.
        
        Args:
            key: Key to delete
            
        Returns:
            True if deleted, False if not found
        """
        for i, meta in enumerate(self.fallback_metadata):
            if meta.get("key") == key:
                self.fallback_metadata.pop(i)
                self.fallback_vectors = np.delete(self.fallback_vectors, i, axis=0)
                self._save_storage()
                return True
        
        return False
    
    def clear(self):
        """Clear all memories."""
        self.fallback_vectors = np.array([]).reshape(0, self.dimension)
        self.fallback_metadata = []
        self._save_storage()
    
    def count(self) -> int:
        """Get total number of stored memories.
        
        Returns:
            Number of entries
        """
        return len(self.fallback_metadata)
    
    def health_check(self) -> Dict[str, Any]:
        """Check health status.
        
        Returns:
            Health status dictionary
        """
        return {
            "model_available": self.has_model,
            "embedding_model": self.embedding_model,
            "storage_dir": str(self.storage_dir),
            "collection_name": self.collection_name,
            "dimension": self.dimension,
            "entry_count": self.count(),
        }
