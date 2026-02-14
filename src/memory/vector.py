"""Vector memory with ONNX embedding backend for semantic search.

Uses onnxruntime for fast, lightweight inference with ONNX models.
Stores embeddings in local NumPy format for simplicity.
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
    """ONNX-based vector memory with semantic search.
    
    Uses onnxruntime for fast, lightweight inference.
    Stores embeddings in local NumPy format.
    """
    
    def __init__(
        self,
        collection_name: str = "efp_memory",
        storage_dir: str = "~/.efp/workspace/vector_memory",
        embedding_model: str = "Xenova/all-MiniLM-L6-v2",
        dimension: int = 384,
        score_threshold: float = 0.5,
        max_tokens: int = 256,
    ):
        """Initialize vector memory.
        
        Args:
            collection_name: Storage collection name
            storage_dir: Directory for persistent storage
            embedding_model: ONNX model name (HuggingFace Xenova format)
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
        
        self.ort_session = None
        self.tokenizer = None
        self._init_onnx()
        
        # Initialize numpy-based storage
        self._init_storage()
    
    def _init_onnx(self):
        """Initialize ONNX runtime session."""
        self.has_onnx = False
        
        try:
            import onnxruntime as ort
            from huggingface_hub import hf_hub_download
            
            cache_dir = self.storage_dir / "models" / self.embedding_model.replace("/", "_")
            cache_dir.mkdir(parents=True, exist_ok=True)
            
            # Try loading Xenova's ONNX model
            if self.embedding_model.startswith("Xenova/"):
                try:
                    model_path = hf_hub_download(
                        repo_id=self.embedding_model,
                        filename="model.onnx",
                        cache_dir=str(cache_dir)
                    )
                    tokenizer_path = hf_hub_download(
                        repo_id=self.embedding_model,
                        filename="tokenizer.json",
                        cache_dir=str(cache_dir)
                    )
                    
                    # Load ONNX session
                    providers = ['CPUExecutionProvider']
                    self.ort_session = ort.InferenceSession(model_path, providers=providers)
                    
                    # Load tokenizer
                    with open(tokenizer_path, 'r', encoding='utf-8') as f:
                        tokenizer_data = json.load(f)
                    
                    vocab = tokenizer_data.get('model', {}).get('vocab', {})
                    
                    def simple_tokenize(text: str) -> np.ndarray:
                        """Simple word-level tokenization."""
                        words = text.lower().split()[:self.max_tokens]
                        return np.array([[vocab.get(w, 0) for w in words]], dtype=np.int64)
                    
                    self.tokenizer = simple_tokenize
                    self.has_onnx = True
                    logger.info(f"ONNX model loaded: {self.embedding_model}")
                    
                except Exception as e:
                    logger.debug(f"Failed to load Xenova ONNX model: {e}")
            
            # Fallback: try loading as standard transformers model
            if not self.has_onnx:
                try:
                    from transformers import AutoTokenizer
                    from optimum.onnxruntime import ORTModelForFeatureExtraction
                    
                    model = ORTModelForFeatureExtraction.from_pretrained(
                        self.embedding_model,
                        export=False,
                        cache_dir=str(cache_dir)
                    )
                    self.ort_session = model.session
                    self.tokenizer = AutoTokenizer.from_pretrained(
                        self.embedding_model,
                        cache_dir=str(cache_dir)
                    )
                    self.has_onnx = True
                    logger.info(f"ONNX model loaded: {self.embedding_model}")
                    
                except ImportError:
                    logger.debug("optimum/transformers not available")
                except Exception as e:
                    logger.debug(f"Failed to load ONNX model: {e}")
            
        except ImportError:
            logger.debug("onnxruntime not installed")
        except Exception as e:
            logger.debug(f"ONNX initialization failed: {e}")
        
        if not self.has_onnx:
            logger.info("Using fallback hash-based embeddings")
    
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
    
    def _get_embedding(self, text: str) -> List[float]:
        """Get embedding for text.
        
        Args:
            text: Text to embed
            
        Returns:
            Embedding vector as list of floats
        """
        if self.has_onnx and self.ort_session:
            try:
                # Tokenize
                if callable(self.tokenizer):
                    input_ids = self.tokenizer(text)
                else:
                    inputs = self.tokenizer(
                        text,
                        padding=True,
                        truncation=True,
                        max_length=self.max_tokens,
                        return_tensors="np"
                    )
                    input_ids = inputs["input_ids"]
                
                # Ensure correct shape
                if len(input_ids.shape) == 1:
                    input_ids = input_ids.reshape(1, -1)
                
                # Run inference
                input_name = self.ort_session.get_inputs()[0].name
                outputs = self.ort_session.run(None, {input_name: input_ids})
                
                # Get last hidden state and apply mean pooling
                hidden_states = outputs[0]
                attention_mask = np.ones_like(input_ids)
                
                mask_expanded = np.expand_dims(attention_mask, -1)
                sum_embeddings = np.sum(hidden_states * mask_expanded, axis=1)
                sum_mask = np.clip(attention_mask.sum(axis=1), a_min=1e-9)
                embedding = sum_embeddings / sum_mask
                
                # Normalize
                norm = np.linalg.norm(embedding, axis=1, keepdims=True)
                normalized = embedding / norm
                
                return normalized[0].astype(np.float32).tolist()
                
            except Exception as e:
                logger.debug(f"ONNX embedding failed: {e}")
        
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
            "onnx_available": self.has_onnx,
            "embedding_model": self.embedding_model,
            "storage_dir": str(self.storage_dir),
            "collection_name": self.collection_name,
            "dimension": self.dimension,
            "entry_count": self.count(),
        }
