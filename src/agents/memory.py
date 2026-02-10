"""Memory system for loading workspace MD files.

Loads SOUL.md, USER.md, AGENTS.md, TOOLS.md, MEMORY.md, and daily notes.
Integrates with VectorMemory for semantic search.
"""

import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Default workspace paths
DEFAULT_WORKSPACE = Path.home() / ".efp" / "workspace"


class MemorySystem:
    """Manages loading and access to workspace memory files."""
    
    def __init__(
        self,
        workspace_path: Optional[str] = None,
        cache_ttl_seconds: int = 60,
        vector_enabled: bool = True,
        vector_config: Optional[Dict] = None,
    ):
        """Initialize memory system.
        
        Args:
            workspace_path: Path to workspace directory. Defaults to ~/.efp/workspace
            cache_ttl_seconds: Cache TTL in seconds (default: 60). Set to 0 to disable caching.
            vector_enabled: Whether to enable vector memory for semantic search
            vector_config: Configuration for vector memory
        """
        self.workspace = Path(workspace_path) if workspace_path else DEFAULT_WORKSPACE
        self._cache: Dict[str, Any] = {}
        self._cache_time: Optional[datetime] = None
        self._cache_ttl_seconds = cache_ttl_seconds
        
        # Initialize vector memory
        self.vector_memory = None
        self._vector_enabled = vector_enabled
        if vector_enabled:
            self._init_vector_memory(vector_config or {})
    
    def _init_vector_memory(self, config: Dict) -> None:
        """Initialize vector memory.
        
        Args:
            config: Vector memory configuration
        """
        try:
            from src.memory.vector import VectorMemory
            
            storage_dir = config.get("storage_dir", str(self.workspace / "vector_memory"))
            embedding_model = config.get("model", "all-MiniLM-L6-v2")
            dimension = config.get("dimension", 384)
            score_threshold = config.get("score_threshold", 0.5)
            
            self.vector_memory = VectorMemory(
                storage_dir=storage_dir,
                embedding_model=embedding_model,
                dimension=dimension,
                score_threshold=score_threshold,
            )
            self._vector_enabled = True
            logger.info("Vector memory initialized successfully")
            
            # Index existing memory files
            self._index_memory_files()
            
        except Exception as e:
            logger.debug(f"Failed to initialize vector memory: {e}")
            self._vector_enabled = False
            self.vector_memory = None
    
    def _index_memory_files(self) -> None:
        """Index all memory files into vector store."""
        if not self.vector_memory:
            return
        
        files = ["SOUL.md", "USER.md", "AGENTS.md", "TOOLS.md", "MEMORY.md"]
        
        for filename in files:
            filepath = self.workspace / filename
            if filepath.exists():
                try:
                    content = filepath.read_text(encoding='utf-8')
                    key = f"memory:{filename}"
                    self.vector_memory.add(
                        key=key,
                        content=content,
                        metadata={"source": filename, "type": "memory_file"},
                    )
                except Exception as e:
                    logger.debug(f"Failed to index {filename}: {e}")
    
    def search_semantic(
        self,
        query: str,
        limit: int = 5,
        score_threshold: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        """Search memories using semantic similarity.
        
        Args:
            query: Search query
            limit: Maximum results
            score_threshold: Minimum similarity score
            
        Returns:
            List of matching entries with scores
        """
        if not self._vector_enabled or not self.vector_memory:
            return []
        
        try:
            return self.vector_memory.search(query, limit, score_threshold)
        except Exception as e:
            logger.error(f"Semantic search failed: {e}")
            return []
    
    def add_memory(
        self,
        key: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Add a memory entry to vector store.
        
        Args:
            key: Unique key for the memory
            content: Content to store
            metadata: Optional metadata
        """
        if not self._vector_enabled or not self.vector_memory:
            return
        
        try:
            self.vector_memory.add(key=key, content=content, metadata=metadata)
        except Exception as e:
            logger.error(f"Failed to add memory: {e}")
    
    def _load_file(self, filename: str) -> Optional[str]:
        """Load a markdown file from workspace.
        
        Args:
            filename: Name of the file (e.g., "SOUL.md")
            
        Returns:
            File contents or None if not found
        """
        filepath = self.workspace / filename
        try:
            if filepath.exists():
                with open(filepath, 'r', encoding='utf-8') as f:
                    return f.read()
        except Exception as e:
            logger.debug(f"Could not load {filename}: {e}")
        return None
    
    def _get_cached(self, key: str, loader) -> str:
        """Get value from cache or load it.
        
        Args:
            key: Cache key
            loader: Function to load the value
            
        Returns:
            Loaded value
        """
        # If caching is disabled (TTL=0), always reload
        if self._cache_ttl_seconds <= 0:
            return loader()
        
        now = datetime.now()
        
        # Check if cache is stale
        if (self._cache_time is None or 
            (now - self._cache_time).total_seconds() > self._cache_ttl_seconds):
            self._cache.clear()
            self._cache_time = now
        
        if key not in self._cache:
            self._cache[key] = loader()
        
        return self._cache[key]
    
    def load_soul(self) -> str:
        """Load SOUL.md - Who the assistant is.
        
        Returns:
            The SOUL.md content for system prompt
        """
        return self._get_cached("soul", lambda: self._load_file("SOUL.md") or "")
    
    def load_user(self) -> str:
        """Load USER.md - Who the human is.
        
        Returns:
            The USER.md content
        """
        return self._get_cached("user", lambda: self._load_file("USER.md") or "")
    
    def load_agents(self) -> str:
        """Load AGENTS.md - Workspace conventions.
        
        Returns:
            The AGENTS.md content for system prompt
        """
        return self._get_cached("agents", lambda: self._load_file("AGENTS.md") or "")
    
    def load_tools_config(self) -> str:
        """Load TOOLS.md - Tool configurations.
        
        Returns:
            The TOOLS.md content
        """
        return self._get_cached("tools", lambda: self._load_file("TOOLS.md") or "")
    
    def load_memory(self) -> str:
        """Load MEMORY.md - Long-term curated memory.
        
        Returns:
            The MEMORY.md content (may be empty for security in non-main sessions)
        """
        return self._get_cached("memory", lambda: self._load_file("MEMORY.md") or "")
    
    def load_daily_notes(self, days: int = 2) -> str:
        """Load recent daily notes from memory/ directory.
        
        Args:
            days: Number of days to include (default: 2)
            
        Returns:
            Combined daily notes content
        """
        memory_dir = self.workspace / "memory"
        if not memory_dir.exists():
            return ""
        
        notes = []
        today = datetime.now()
        
        for i in range(days):
            date = today - timedelta(days=i)
            note_file = memory_dir / f"{date.strftime('%Y-%m-%d')}.md"
            if note_file.exists():
                try:
                    with open(note_file, 'r', encoding='utf-8') as f:
                        notes.append(f"=== {date.strftime('%Y-%m-%d')} ===\n{f.read()}")
                except Exception:
                    pass
        
        return "\n\n".join(notes) if notes else ""
    
    def build_system_prompt(self, include_memory: bool = True) -> str:
        """Build complete system prompt from all memory files.
        
        Args:
            include_memory: Whether to include MEMORY.md (should be False for non-main sessions)
            
        Returns:
            Complete system prompt
        """
        parts = []
        
        # Load core files
        soul = self.load_soul()
        agents = self.load_agents()
        user = self.load_user()
        tools = self.load_tools_config()
        memory = self.load_memory() if include_memory else ""
        daily_notes = self.load_daily_notes()
        
        # Build prompt sections
        if soul:
            parts.append(f"=== SOUL (Who You Are) ===\n{soul}")
        
        if agents:
            parts.append(f"=== AGENTS (Workspace Conventions) ===\n{agents}")
        
        if user:
            parts.append(f"=== USER (Who You're Helping) ===\n{user}")
        
        if tools:
            parts.append(f"=== TOOLS (Your Configuration) ===\n{tools}")
        
        if include_memory and memory:
            parts.append(f"=== LONG-TERM MEMORY ===\n{memory}")
        
        if daily_notes:
            parts.append(f"=== RECENT CONTEXT (Daily Notes) ===\n{daily_notes}")
        
        return "\n\n".join(parts)
    
    def clear_cache(self):
        """Clear the memory cache (forces reload on next access)."""
        self._cache.clear()
        self._cache_time = None


# Global memory system instance
memory_system = MemorySystem()
