"""Memory system for loading workspace MD files.

Loads SOUL.md, USER.md, AGENTS.md, TOOLS.md, MEMORY.md, and daily notes.
Integrates with LightweightMemory for TF-IDF search.
Supports chunk-level indexing with auto-refresh on file changes.
"""

import logging
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from src.utils.truncate import truncate

# Default workspace paths
DEFAULT_WORKSPACE = Path.home() / ".efp" / "workspace"

# Core memory files to index
CORE_MEMORY_FILES = ["SOUL.md", "USER.md", "AGENTS.md", "TOOLS.md", "MEMORY.md"]


class MemorySystem:
    """Manages loading and access to workspace memory files."""
    
    # Source registry - defines which files to index
    SOURCE_REGISTRY = {
        "core": CORE_MEMORY_FILES,
        "daily": "memory/*.md",  # Glob pattern for daily notes
    }
    
    def __init__(
        self,
        workspace_path: Optional[str] = None,
        cache_ttl_seconds: int = 60,
        search_enabled: bool = True,
        search_config: Optional[Dict] = None,
        daily_notes_index_days: int = 14,
        daily_notes_inject_days: int = 2,
    ):
        """Initialize memory system.
        
        Args:
            workspace_path: Path to workspace directory. Defaults to ~/.efp/workspace
            cache_ttl_seconds: Cache TTL in seconds (default: 60)
            search_enabled: Whether to enable memory search
            search_config: Configuration for memory search
            daily_notes_index_days: Number of days to index for daily notes (default: 14)
            daily_notes_inject_days: Number of days to inject in prompt (default: 2)
        """
        self.workspace = Path(workspace_path) if workspace_path else DEFAULT_WORKSPACE
        self._cache: Dict[str, Any] = {}
        self._cache_time: Optional[datetime] = None
        self._cache_ttl_seconds = cache_ttl_seconds
        
        # Daily notes configuration
        self.daily_notes_index_days = daily_notes_index_days
        self.daily_notes_inject_days = daily_notes_inject_days
        
        # Source mtimes for auto-refresh tracking
        self._source_mtimes: Dict[str, float] = {}
        
        # Initialize lightweight search
        self.search_enabled = search_enabled
        self.search_config = search_config or {}
        self._init_search()
    
    def _init_search(self) -> None:
        """Initialize lightweight memory search."""
        if not self.search_enabled:
            return
            
        try:
            from src.memory.lightweight import LightweightMemory
            
            storage_dir = self.search_config.get("storage_dir", str(self.workspace / "memory_search"))
            score_threshold = self.search_config.get("score_threshold", 0.1)
            
            self.search_memory = LightweightMemory(
                storage_dir=storage_dir,
                score_threshold=score_threshold,
            )
            logger.info("Lightweight memory search initialized")
            
            # Index existing memory files
            self._index_memory_files()
            
        except Exception as e:
            logger.debug(f"Failed to initialize search: {e}")
            self.search_memory = None
    
    def _index_memory_files(self) -> None:
        """Index memory files into search using chunking."""
        if not self.search_memory:
            return
        
        try:
            from src.memory.chunking import chunk_markdown
        except ImportError:
            logger.warning("chunking module not available, using whole-file indexing")
            chunk_markdown = None
        
        # Index core memory files
        for filename in CORE_MEMORY_FILES:
            filepath = self.workspace / filename
            if filepath.exists():
                self._index_file(filepath, filename, chunk_markdown)
        
        # Index daily notes
        self._index_daily_notes(chunk_markdown)
        
        logger.info(f"Indexed memory files: {len(self._source_mtimes)} sources")
    
    def _index_file(
        self,
        filepath: Path,
        source_name: str,
        chunk_markdown=None,
        kind: str = "core",
        date: str = None,
    ) -> None:
        """Index a single file, optionally using chunking.
        
        Args:
            filepath: Path to the file
            source_name: Name for the source (e.g., "MEMORY.md")
            chunk_markdown: Optional chunking function
            kind: Type of file ("core" or "daily")
            date: Date for daily notes (YYYY-MM-DD format)
        """
        if not self.search_memory:
            return
        
        try:
            mtime = os.path.getmtime(filepath)
            content = filepath.read_text(encoding='utf-8')
            
            if chunk_markdown:
                # Use chunking for better retrieval
                chunks = chunk_markdown(
                    text=content,
                    source_name=source_name,
                    kind=kind,
                    date=date,
                    max_chars=1200,
                    min_chars=200,
                )
                
                # Delete old chunks for this source
                self.search_memory.delete_by_source(source_name)
                
                # Add new chunks
                for chunk in chunks:
                    self.search_memory.upsert(
                        entry_id=chunk.id,
                        content=chunk.text,
                        metadata=chunk.meta,
                        mtime=mtime,
                    )
            else:
                # Fallback to whole-file indexing
                key = f"mem:{source_name}"
                self.search_memory.upsert(
                    entry_id=key,
                    content=content,
                    metadata={"source": source_name, "kind": kind},
                    mtime=mtime,
                )
            
            # Track mtime
            self._source_mtimes[source_name] = mtime
            
        except Exception as e:
            logger.debug(f"Failed to index {source_name}: {e}")
    
    def _index_daily_notes(self, chunk_markdown=None) -> None:
        """Index daily notes from workspace/memory/*.md.
        
        Args:
            chunk_markdown: Optional chunking function
        """
        if not self.search_memory:
            return
        
        memory_dir = self.workspace / "memory"
        if not memory_dir.exists():
            return
        
        # Get date range (include today)
        end_date = datetime.now()
        start_date = end_date - timedelta(days=self.daily_notes_index_days - 1)
        
        # Find daily note files
        for i in range(self.daily_notes_index_days):
            date = start_date + timedelta(days=i)
            date_str = date.strftime("%Y-%m-%d")
            filename = f"{date_str}.md"
            filepath = memory_dir / filename
            
            if filepath.exists():
                source_name = filepath.name  # e.g., "2026-03-03.md"
                self._index_file(filepath, source_name, chunk_markdown, kind="daily", date=date_str)
    
    def refresh_index_if_needed(self) -> bool:
        """Refresh index if any source files have changed.
        
        Returns:
            True if any files were refreshed, False otherwise
        """
        if not self.search_memory:
            return False
        
        refreshed = False
        
        # Check core files
        for filename in CORE_MEMORY_FILES:
            filepath = self.workspace / filename
            if filepath.exists():
                try:
                    mtime = os.path.getmtime(filepath)
                    stored_mtime = self._source_mtimes.get(filename)
                    
                    if stored_mtime is None or mtime > stored_mtime:
                        logger.info(f"Refreshing index for {filename} (mtime changed)")
                        try:
                            from src.memory.chunking import chunk_markdown
                        except ImportError:
                            chunk_markdown = None
                        
                        self._index_file(filepath, filename, chunk_markdown, kind="core")
                        refreshed = True
                except Exception as e:
                    logger.debug(f"Error checking {filename}: {e}")
        
        # Check daily notes
        memory_dir = self.workspace / "memory"
        if memory_dir.exists():
            end_date = datetime.now()
            start_date = end_date - timedelta(days=self.daily_notes_index_days - 1)
            
            for i in range(self.daily_notes_index_days):
                date = start_date + timedelta(days=i)
                date_str = date.strftime("%Y-%m-%d")
                filename = f"{date_str}.md"
                filepath = memory_dir / filename
                
                if filepath.exists():
                    try:
                        mtime = os.path.getmtime(filepath)
                        stored_mtime = self._source_mtimes.get(filename)
                        
                        if stored_mtime is None or mtime > stored_mtime:
                            logger.info(f"Refreshing daily note: {filename}")
                            try:
                                from src.memory.chunking import chunk_markdown
                            except ImportError:
                                chunk_markdown = None
                            
                            # Extract date from filename (e.g., "2026-03-03.md")
                            date_str = filename.replace('.md', '')
                            self._index_file(filepath, filename, chunk_markdown, kind="daily", date=date_str)
                            refreshed = True
                    except Exception as e:
                        logger.debug(f"Error checking {filename}: {e}")
        
        # Cleanup: remove chunks for deleted core files
        for filename in CORE_MEMORY_FILES:
            filepath = self.workspace / filename
            if not filepath.exists() and filename in self._source_mtimes:
                logger.info(f"Removing index for deleted file: {filename}")
                self.search_memory.delete_by_source(filename)
                del self._source_mtimes[filename]
                refreshed = True
        
        # Cleanup: remove chunks for daily notes outside the date range
        if memory_dir.exists():
            end_date = datetime.now()
            start_date = end_date - timedelta(days=self.daily_notes_index_days - 1)
            
            # Get all daily note files in current range
            current_daily = set()
            for i in range(self.daily_notes_index_days):
                date = start_date + timedelta(days=i)
                date_str = date.strftime("%Y-%m-%d")
                current_daily.add(f"{date_str}.md")
            
            # Delete chunks for daily notes not in range
            for filename in list(self._source_mtimes.keys()):
                if filename.endswith('.md') and filename not in current_daily:
                    # Check if it's a daily note (not a core file)
                    if self._source_mtimes.get(filename):
                        meta_check = self.search_memory.get_entry(
                            f"daily:{filename}#"
                        ) if self.search_memory else None
                        if meta_check or filename.startswith("20"):
                            logger.info(f"Removing index for out-of-range daily note: {filename}")
                            self.search_memory.delete_by_source(filename)
                            del self._source_mtimes[filename]
                            refreshed = True
        
        return refreshed
    
    def search(
        self,
        query: str,
        limit: int = 5,
        score_threshold: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        """Search memories using TF-IDF scoring.
        
        Args:
            query: Search query
            limit: Maximum results
            score_threshold: Minimum score
            
        Returns:
            List of matching entries with scores
        """
        if not self.search_memory:
            return []
        
        # Auto-refresh index if needed
        self.refresh_index_if_needed()
        
        try:
            threshold = score_threshold or self.search_config.get("score_threshold", 0.1)
            results = self.search_memory.search(query, limit)
            return [r for r in results if r["score"] >= threshold]
        except Exception as e:
            logger.debug(f"Search failed: {e}")
            return []
    
    def add_memory(
        self,
        key: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Add a memory entry to search index.
        
        Args:
            key: Unique key for the memory
            content: Content to store
            metadata: Optional metadata
        """
        if not self.search_memory:
            return
        
        try:
            self.search_memory.upsert(
                entry_id=key,
                content=content,
                metadata=metadata,
                mtime=time.time(),
            )
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
    
    def load_daily_notes(self, days: Optional[int] = None) -> str:
        """Load recent daily notes from memory/ directory.
        
        Args:
            days: Number of days to include (default: self.daily_notes_inject_days)
            
        Returns:
            Combined daily notes content with date labels
        """
        memory_dir = self.workspace / "memory"
        if not memory_dir.exists():
            return ""
        
        # Use config days if not specified
        days = days if days is not None else self.daily_notes_inject_days
        
        notes = []
        today = datetime.now()
        
        for i in range(days):
            date = today - timedelta(days=i)
            note_file = memory_dir / f"{date.strftime('%Y-%m-%d')}.md"
            if note_file.exists():
                try:
                    content = note_file.read_text(encoding='utf-8')
                    # Limit per-day content to avoid too long prompts
                    if len(content) > 1500:
                        content = content[:1500] + "..."
                    date_str = date.strftime('%Y-%m-%d')
                    notes.append(f"=== {date_str} ===\n{content}")
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
    
    def build_context_with_search(
        self,
        query: str,
        include_memory: bool = True,
        limit: int = 3,
        score_threshold: Optional[float] = None,
    ) -> str:
        """Build context section with search results.
        
        Args:
            query: User message to search for
            include_memory: Include MEMORY.md in context
            limit: Maximum search results
            score_threshold: Minimum similarity score
            
        Returns:
            Context section with search results
        """
        parts = []
        
        # Perform search
        search_results = self.search(query, limit, score_threshold)
        
        if search_results:
            context_parts = []
            for i, result in enumerate(search_results, 1):
                source = result.get('metadata', {}).get('source', 'Unknown')
                content = truncate(result.get('content', ''), 500)
                score = result.get('score', 0)
                context_parts.append(f"[{i}] {source} (relevance: {score:.2f})\n{content}")
            
            parts.append("=== RELEVANT CONTEXT (from memory search) ===\n")
            parts.append("Relevant information found:\n\n")
            parts.append("\n\n---\n\n".join(context_parts))
            parts.append("\n\nUse the above context if relevant to the user's query.")
        
        return "\n".join(parts)
    
    def clear_cache(self):
        """Clear the memory cache (forces reload on next access)."""
        self._cache.clear()
        self._cache_time = None


# Global memory system instance
memory_system = MemorySystem()
