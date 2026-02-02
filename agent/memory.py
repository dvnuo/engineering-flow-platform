"""Memory system for loading workspace MD files.

Loads SOUL.md, USER.md, AGENTS.md, TOOLS.md, MEMORY.md, and daily notes.
"""

import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Default workspace paths
DEFAULT_WORKSPACE = Path.home() / ".openclaw" / "workspace"


class MemorySystem:
    """Manages loading and access to workspace memory files."""
    
    def __init__(self, workspace_path: Optional[str] = None):
        """Initialize memory system.
        
        Args:
            workspace_path: Path to workspace directory. Defaults to ~/.openclaw/workspace
        """
        self.workspace = Path(workspace_path) if workspace_path else DEFAULT_WORKSPACE
        self._cache: Dict[str, Any] = {}
        self._cache_time: Optional[datetime] = None
        self._cache_ttl_seconds = 60  # Cache for 60 seconds
    
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
