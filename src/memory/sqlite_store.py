"""SQLite-based memory store with vector search support.

Features:
- SQLite storage for memory chunks
- Vector embedding cache (no actual vectors yet)
- Hybrid search ready for BM25 integration
- Session transcript support (optional)
"""

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import MemoryConfig, MemoryStore, get_memory_path, get_long_term_memory_path

logger = logging.getLogger(__name__)


class SqliteMemoryStore(MemoryStore):
    """SQLite-based memory store with vector search support."""
    
    def __init__(self, config: Optional[MemoryConfig] = None):
        self.config = config or MemoryConfig()
        self.db_path = self.config.memory_dir / "memories.sqlite"
        self._connection: Optional[sqlite3.Connection] = None
        super().__init__(config)
    
    def _init_store(self):
        """Initialize SQLite database and tables."""
        self._connection = sqlite3.connect(str(self.db_path))
        self._connection.row_factory = sqlite3.Row
        
        # Create tables
        self._create_tables()
        
        logger.info(f"SQLite memory store initialized: {self.db_path}")
    
    def _create_tables(self):
        """Create necessary tables."""
        cursor = self._connection.cursor()
        
        # Memories table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                source TEXT,
                memory_type TEXT DEFAULT 'general',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                metadata TEXT
            )
        """)
        
        # Embeddings cache table (stores embedding metadata, not actual vectors)
        # Note: Actual vector storage requires:
        # - Option A: sqlite-vec extension (native, fast)
        # - Option B: External vector DB (ChromaDB, Weaviate, etc.)
        # - Option C: In-memory numpy arrays (simple, no deps)
        # This table caches embedding provider/model info for future vector search.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS embeddings (
                memory_id TEXT PRIMARY KEY,
                provider TEXT,
                model TEXT,
                chunk_hash TEXT,
                cached_at TEXT NOT NULL,
                FOREIGN KEY (memory_id) REFERENCES memories(id)
            )
        """)
        
        # Full-text search virtual table for BM25
        cursor.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
                content,
                tokenize='porter'
            )
        """)
        
        # Index for faster lookups
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_memories_type
            ON memories(memory_type)
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_memories_created
            ON memories(created_at)
        """)
        
        self._connection.commit()
    
    def _generate_id(self) -> str:
        """Generate a unique memory ID."""
        import hashlib
        timestamp = datetime.now().isoformat()
        random_suffix = hashlib.md5(f"{timestamp}".encode()).hexdigest()[:8]
        return f"mem_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{random_suffix}"
    
    async def add_memory(
        self,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
        memory_type: str = "general",
        source: str = "manual"
    ) -> str:
        """Add a memory chunk to the store.
        
        Args:
            content: Memory content text.
            metadata: Optional metadata dictionary.
            memory_type: Type of memory (daily, long_term, session, etc.)
            source: Source of the memory.
            
        Returns:
            Memory ID.
        """
        memory_id = self._generate_id()
        now = datetime.now().isoformat()
        
        cursor = self._connection.cursor()
        
        # Insert into memories table
        cursor.execute("""
            INSERT INTO memories (id, content, source, memory_type, created_at, updated_at, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            memory_id,
            content,
            source,
            memory_type,
            now,
            now,
            json.dumps(metadata) if metadata else None
        ))
        
        # Insert into FTS table for full-text search
        cursor.execute("""
            INSERT INTO memories_fts (rowid, content)
            VALUES ((SELECT rowid FROM memories WHERE id = ?), ?)
        """, (memory_id, content))
        
        self._connection.commit()
        
        logger.info(f"Added memory: {memory_id} (type={memory_type})")
        return memory_id
    
    async def search(
        self,
        query: str,
        limit: int = 5,
        memory_type: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Search memories using full-text search.
        
        Note: Currently uses FTS5 BM25 full-text search.
        When vector search is added, results will be fused using:
          finalScore = vector_weight * vectorScore + text_weight * textScore
        
        Args:
            query: Search query.
            limit: Maximum number of results.
            memory_type: Optional filter by memory type.
            
        Returns:
            List of matching memories with normalized scores.
            Score is normalized to 0-1 range (higher is better).
        """
        cursor = self._connection.cursor()
        
        # Build query
        sql = """
            SELECT m.id, m.content, m.source, m.memory_type, m.created_at, m.metadata,
                   bm25(memories_fts) as bm25_score
            FROM memories_fts
            JOIN memories m ON memories_fts.rowid = m.rowid
            WHERE memories_fts MATCH ?
        """
        params = [query]
        
        if memory_type:
            sql += " AND m.memory_type = ?"
            params.append(memory_type)
        
        sql += " ORDER BY bm25_score LIMIT ?"
        params.append(limit)
        
        cursor.execute(sql, params)
        
        results = []
        for row in cursor.fetchall():
            # Normalize BM25 score to 0-1 range (higher is better)
            # BM25: lower is better, so we invert: 1 / (1 + bm25)
            # Then apply text_weight for future hybrid search
            bm25_score = row["bm25_score"]
            normalized_score = (1.0 / (1.0 + bm25_score)) * self.config.text_weight
            
            results.append({
                "id": row["id"],
                "content": row["content"],
                "source": row["source"],
                "memory_type": row["memory_type"],
                "created_at": row["created_at"],
                "metadata": json.loads(row["metadata"]) if row["metadata"] else None,
                "score": normalized_score,
                "raw_score": bm25_score,  # Keep raw BM25 for debugging
                "search_type": "bm25"
            })
        
        logger.debug(f"Search '{query}': {len(results)} results")
        return results
    
    async def get_memory(self, memory_id: str) -> Optional[Dict[str, Any]]:
        """Get a specific memory by ID.
        
        Args:
            memory_id: Memory ID to retrieve.
            
        Returns:
            Memory dict or None if not found.
        """
        cursor = self._connection.cursor()
        
        cursor.execute("""
            SELECT id, content, source, memory_type, created_at, updated_at, metadata
            FROM memories WHERE id = ?
        """, (memory_id,))
        
        row = cursor.fetchone()
        
        if not row:
            return None
        
        return {
            "id": row["id"],
            "content": row["content"],
            "source": row["source"],
            "memory_type": row["memory_type"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "metadata": json.loads(row["metadata"]) if row["metadata"] else None
        }
    
    async def delete_memory(self, memory_id: str) -> bool:
        """Delete a memory by ID.
        
        Args:
            memory_id: Memory ID to delete.
            
        Returns:
            True if deleted, False if not found.
        """
        cursor = self._connection.cursor()
        
        # Delete from FTS first
        cursor.execute("""
            DELETE FROM memories_fts
            WHERE rowid IN (SELECT rowid FROM memories WHERE id = ?)
        """, (memory_id,))
        
        # Delete from memories
        cursor.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        
        self._connection.commit()
        
        deleted = cursor.rowcount > 0
        
        if deleted:
            logger.info(f"Deleted memory: {memory_id}")
        
        return deleted
    
    async def list_memories(
        self,
        limit: int = 100,
        memory_type: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """List memories with optional type filter.
        
        Args:
            limit: Maximum number of results.
            memory_type: Optional filter by memory type.
            
        Returns:
            List of memories.
        """
        cursor = self._connection.cursor()
        
        sql = """
            SELECT id, content, source, memory_type, created_at, updated_at, metadata
            FROM memories
        """
        
        params = []
        if memory_type:
            sql += " WHERE memory_type = ?"
            params.append(memory_type)
        
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        
        cursor.execute(sql, params)
        
        results = []
        for row in cursor.fetchall():
            results.append({
                "id": row["id"],
                "content": row["content"],
                "source": row["source"],
                "memory_type": row["memory_type"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "metadata": json.loads(row["metadata"]) if row["metadata"] else None
            })
        
        return results
    
    async def import_markdown_memories(self) -> int:
        """Import memories from Markdown files.
        
        Imports from:
        - memory/YYYY-MM-DD.md (daily notes)
        - MEMORY.md (long-term memory)
        
        Returns:
            Number of memories imported.
        """
        imported = 0
        
        # Import daily memories
        memory_dir = self.config.workspace_dir / "memory"
        if memory_dir.exists():
            for md_file in memory_dir.glob("*.md"):
                if md_file.name.startswith("20"):  # Date pattern YYYY-MM-DD
                    with open(md_file, "r", encoding="utf-8") as f:
                        content = f.read()
                    
                    # Split into chunks (simple paragraph-based)
                    chunks = [p.strip() for p in content.split("\n\n") if p.strip()]
                    
                    for chunk in chunks:
                        await self.add_memory(
                            content=chunk,
                            memory_type="daily",
                            source=str(md_file)
                        )
                        imported += 1
        
        # Import long-term memory
        long_term_path = self.config.workspace_dir / "MEMORY.md"
        if long_term_path.exists():
            with open(long_term_path, "r", encoding="utf-8") as f:
                content = f.read()
            
            chunks = [p.strip() for p in content.split("\n\n") if p.strip()]
            
            for chunk in chunks:
                await self.add_memory(
                    content=chunk,
                    memory_type="long_term",
                    source=str(long_term_path)
                )
                imported += 1
        
        logger.info(f"Imported {imported} memories from Markdown files")
        return imported
    
    async def close(self):
        """Close the database connection gracefully."""
        if self._connection:
            self._connection.close()
            self._connection = None
            logger.info("SQLite memory store closed")
    
    def __enter__(self):
        """Context manager entry."""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - ensures connection is closed."""
        # Create a new event loop for synchronous cleanup
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self.close())
            loop.close()
        except Exception as e:
            logger.error(f"Error closing memory store: {e}")
        return False
    
    def __del__(self):
        """Cleanup on deletion - graceful shutdown."""
        try:
            if self._connection:
                self._connection.close()
                self._connection = None
        except Exception:
            pass  # Ignore errors during interpreter shutdown
