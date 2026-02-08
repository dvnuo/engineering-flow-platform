"""
Mention Polling Module - Monitor @mentions and process commands.

This module polls GitHub, Jira, and Confluence for comments that mention
the configured users, then processes commands and replies.
Uses long-term memory (SQLite) to track processed issues.
"""

import asyncio
import logging
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set
from dataclasses import dataclass, field

from src.config import config
from src.channels.github import github_channel
from src.channels.jira import jira_channel
from src.channels.confluence import confluence_channel
from src.agents.executor import execute_tool

# Import memory module for tracking processed issues
try:
    from src.memory import get_memory_store
    MEMORY_AVAILABLE = True
except ImportError:
    MEMORY_AVAILABLE = False
    logger.warning("Memory module not available, using in-memory tracking only")

logger = logging.getLogger(__name__)


@dataclass
class Comment:
    """Represents a comment from any platform."""
    id: str
    platform: str
    owner: str        # Username who wrote the comment
    body: str
    resource_id: str  # Issue key, PR number, or page ID
    resource_type: str  # "issue", "pr", "page"
    resource_title: str
    url: str
    created_at: datetime
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Command:
    """Parsed command from a mention."""
    tool_name: str
    args: Dict[str, Any]
    original_text: str


class MentionPoller:
    """Monitor @mentions across platforms and process commands."""
    
    def __init__(self):
        self.last_check: Dict[str, datetime] = {}
        self.running = False
        self._lock = asyncio.Lock()
        
        # Load monitored usernames
        self.monitored_users: Set[str] = set()
        self._load_config()
    
    def _load_config(self):
        """Load configuration."""
        polling = config.get("polling", {})
        self.enabled = polling.get("enabled", False)
        self.interval = polling.get("interval_seconds", 30)
        
        users = polling.get("monitored_usernames", [])
        if isinstance(users, str):
            users = [users]
        self.monitored_users = set(users)
        
        # Platform-specific settings
        self.platforms: Dict[str, Dict] = {
            "github": polling.get("github", {}),
            "jira": polling.get("jira", {}),
            "confluence": polling.get("confluence", {}),
        }
    
    def is_monitored_user(self, username: str) -> bool:
        """Check if a username is being monitored."""
        if not self.monitored_users:
            return False
        return username.lower() in {u.lower() for u in self.monitored_users}
    
    async def start(self):
        """Start the polling loop."""
        if self.running:
            return
        
        self.running = True
        self._load_config()
        
        while self.running:
            try:
                await self._poll_all()
            except Exception as e:
                print(f"Polling error: {e}")
            
            await asyncio.sleep(self.interval)
    
    async def stop(self):
        """Stop the polling loop."""
        self.running = False
    
    async def _poll_all(self):
        """Poll all configured platforms."""
        tasks = []
        
        if self.platforms.get("github", {}).get("enabled", False):
            tasks.append(self._poll_github())
        
        if self.platforms.get("jira", {}).get("enabled", False):
            tasks.append(self._poll_jira())
        
        if self.platforms.get("confluence", {}).get("enabled", False):
            tasks.append(self._poll_confluence())
        
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
    
    async def _poll_github(self):
        """Poll GitHub for @mentions."""
        repos = self.platforms.get("github", {}).get("repos", [])
        if not repos:
            return
        
        for repo in repos:
            try:
                comments = await github_channel.get_recent_issue_comments(
                    repo, 
                    since=self.last_check.get(f"github:{repo}")
                )
                
                for comment in comments:
                    if self._has_mention(comment.get("body", "")):
                        # Extract issue number from comment if available
                        resource = {
                            "key": comment.get("extra", {}).get("issue_number") or comment.get("issue_number"),
                            "owner": comment.get("extra", {}).get("owner"),
                            "repo": repo
                        }
                        await self._process_mention(comment, "github", resource)
                
                self.last_check[f"github:{repo}"] = datetime.utcnow()
            except Exception as e:
                logger.warning(f"GitHub polling error for {repo}: {e}")
    
    async def _poll_jira(self):
        """Poll Jira for @mentions."""
        projects = self.platforms.get("jira", {}).get("projects", [])
        if not projects:
            return
        
        jql = f'project IN ({",".join(projects)}) AND updated >= "-1h"'
        
        try:
            issues_result = await jira_channel.search_issues(jql, max_results=50)
            issues = issues_result.get("issues", [])
            
            for issue in issues:
                issue_key = issue.get("key")
                comments = await jira_channel.get_comments(issue_key)
                
                for comment in comments:
                    if self._has_mention(comment.get("body", "")):
                        await self._process_mention(comment, "jira", issue)
            
        except Exception as e:
            logger.warning(f"Jira polling error: {e}")
    
    async def _poll_confluence(self):
        """Poll Confluence for @mentions."""
        spaces = self.platforms.get("confluence", {}).get("spaces", [])
        if not spaces:
            return
        
        try:
            for space in spaces:
                pages = await confluence.search_pages(
                    f'space = "{space}" AND type = page',
                    limit=20
                )
                
                for page in pages.get("results", []):
                    comments = await confluence_channel.get_comments(page["id"])
                    
                    for comment in comments:
                        if self._has_mention(comment.get("body", "")):
                            await self._process_mention(comment, "confluence", page)
                            
        except Exception as e:
            logger.warning(f"Confluence polling error: {e}")
    
    def _has_mention(self, text: str) -> bool:
        """Check if text contains @mention of monitored users."""
        mentions = self.extract_mentions(text)
        return any(self.is_monitored_user(m) for m in mentions)
    
    @staticmethod
    def extract_mentions(text: str) -> List[str]:
        """Extract @mentions from text."""
        pattern = r'@(\w+)'
        return re.findall(pattern, text, re.IGNORECASE)
    
    async def _is_already_processed(
        self,
        platform: str,
        resource_id: str,
        comment_id: str
    ) -> bool:
        """Check if a mention has already been processed using memory.
        
        Uses long-term memory (SQLite) to track processed issues across restarts.
        Falls back to in-memory tracking if memory is unavailable.
        
        Args:
            platform: Platform name (jira, github, confluence)
            resource_id: Issue key, PR number, or page ID
            comment_id: Comment ID
            
        Returns:
            True if already processed, False otherwise
        """
        # Generate unique memory ID for this mention
        memory_id = f"processed:{platform}:{resource_id}:{comment_id}"
        
        # Check in-memory cache first (fast path)
        if memory_id in self._processed:
            return True
        
        # Check memory store if available
        if MEMORY_AVAILABLE:
            try:
                memory_store = get_memory_store()
                if memory_store:
                    # Use get_memory for exact match (not fuzzy search)
                    memory = await memory_store.get_memory(memory_id)
                    if memory:
                        logger.debug(f"Found in memory: {memory_id}")
                        # Add to in-memory cache
                        self._processed.add(memory_id)
                        return True
            except Exception as e:
                logger.warning(f"Error checking memory for {memory_id}: {e}")
        
        return False
    
    async def _mark_as_processed(
        self,
        platform: str,
        resource_id: str,
        comment_id: str,
        result_summary: str
    ):
        """Mark a mention as processed in memory.
        
        Args:
            platform: Platform name
            resource_id: Issue/PR/page ID
            comment_id: Comment ID
            result_summary: Brief summary of processing result
        """
        memory_id = f"processed:{platform}:{resource_id}:{comment_id}"
        
        # Add to in-memory cache
        self._processed.add(memory_id)
        
        # Persist to memory store if available
        if MEMORY_AVAILABLE:
            try:
                memory_store = get_memory_store()
                if memory_store:
                    content = f"[{datetime.now().isoformat()}] {platform}:{resource_id}: {result_summary}"
                    await memory_store.add_memory(
                        content=content,
                        metadata={
                            "platform": platform,
                            "resource_id": resource_id,
                            "comment_id": comment_id,
                            "result": result_summary
                        },
                        memory_type="processed_mention"
                    )
                    logger.info(f"Saved to memory: {memory_id}")
            except Exception as e:
                logger.warning(f"Error saving to memory: {e}")
    
    async def _process_mention(
        self, 
        comment: Dict, 
        platform: str,
        resource: Optional[Dict] = None
    ):
        """Process a detected mention."""
        comment_body = comment.get("body", "")
        comment_id = str(comment.get("id", ""))
        resource_id = resource.get("key") if resource else None
        
        # Check if already processed using memory
        if resource_id:
            is_processed = await self._is_already_processed(
                platform, resource_id, comment_id
            )
            if is_processed:
                logger.debug(f"Skipping already processed: {platform}:{resource_id}")
                return
        
        async with self._lock:
            # Double-check after acquiring lock
            if resource_id:
                is_processed = await self._is_already_processed(
                    platform, resource_id, comment_id
                )
                if is_processed:
                    return
            
            # Parse command
            cmd = self.parse_command(comment_body, platform)
            
            # Execute command
            try:
                result = await execute_tool(cmd.tool_name, **cmd.args)
                
                # Reply to the comment
                await self._reply_to(comment, result, platform, resource)
                
                # Mark as processed in memory
                if resource_id:
                    await self._mark_as_processed(
                        platform, resource_id, comment_id,
                        f"{cmd.tool_name}: {result[:100]}"
                    )
                
                logger.info(f"Processed: {platform}:{resource_id}: {cmd.tool_name}")
                
            except Exception as e:
                error_msg = f"Error executing command: {e}"
                await self._reply_to(comment, error_msg, platform, resource)
                
                # Still mark as processed to avoid re-processing errors
                if resource_id:
                    await self._mark_as_processed(
                        platform, resource_id, comment_id,
                        f"ERROR: {str(e)[:100]}"
                    )
    
    def parse_command(self, text: str, platform: str) -> Command:
        """Parse a command from mention text."""
        # Remove @mentions
        cmd_text = self._strip_mentions(text)
        cmd_text = cmd_text.strip()
        
        # Parse common commands
        if not cmd_text:
            return Command("help", {}, text)
        
        parts = cmd_text.split()
        first_word = parts[0].lower()
        
        # Help command
        if first_word in ("help", "帮助"):
            return Command("help", {}, text)
        
        # Jira commands
        if platform == "jira":
            if first_word == "create" and len(parts) > 1:
                return Command(
                    "jira_create_issue",
                    self._parse_jira_create(parts[1:]),
                    text
                )
            elif first_word == "status" and len(parts) > 1:
                return Command(
                    "jira_get_issue",
                    {"issue_key": parts[1]},
                    text
                )
        
        # Confluence commands
        if platform == "confluence":
            if first_word == "search" and len(parts) > 1:
                # @user search confluence "query"
                query = " ".join(parts[1:])
                if query.lower().startswith("confluence"):
                    query = query[10:].strip()
                return Command(
                    "confluence_search",
                    {"cql": query},
                    text
                )
        
        # Default: treat as natural language, return help with context
        return Command(
            "help",
            {"context": {"platform": platform, "raw_text": text}},
            text
        )
    
    def _strip_mentions(self, text: str) -> str:
        """Remove @mentions from text."""
        return re.sub(r'@\w+', '', text).strip()
    
    def _parse_jira_create(self, parts: List[str]) -> Dict[str, Any]:
        """Parse jira create command."""
        args = {"project": jira_channel.project, "summary": "", "description": ""}
        
        # Simple parsing: create issue "title" -d "description"
        # or create issue title
        if len(parts) >= 2:
            if parts[0].lower() == "issue":
                parts = parts[1:]
        
        if parts:
            # Title might be quoted
            if parts[0].startswith('"') and parts[0].endswith('"'):
                args["summary"] = parts[0][1:-1]
            else:
                args["summary"] = parts[0]
        
        # Look for -d flag for description
        if "-d" in parts:
            idx = parts.index("-d")
            if idx + 1 < len(parts):
                desc = parts[idx + 1]
                if desc.startswith('"') and desc.endswith('"'):
                    desc = desc[1:-1]
                args["description"] = desc
        
        return args
    
    async def _reply_to(
        self, 
        comment: Dict, 
        result: str, 
        platform: str,
        resource: Optional[Dict] = None
    ):
        """Reply to a comment with the result."""
        author = comment.get("author", "user")
        reply_body = f"@{author} Processing result:\n\n{result}"
        
        # Determine resource ID based on platform
        if platform == "github":
            # For GitHub, we need owner/repo and issue number
            # This would be passed via extra dict
            owner = resource.get("owner") if resource else "unknown"
            repo = resource.get("repo") if resource else "unknown"
            issue_num = comment.get("extra", {}).get("issue_number")
            if issue_num:
                await github_channel.add_comment(owner, repo, issue_num, reply_body)
        
        elif platform == "jira":
            issue_key = resource.get("key") if resource else None
            if issue_key:
                await jira_channel.add_comment(issue_key, reply_body)
        
        elif platform == "confluence":
            page_id = resource.get("id") if resource else None
            if page_id:
                await confluence_channel.add_comment(page_id, reply_body)
    
    # Track processed comments to avoid duplicates
    _processed: Set[str] = set()


# Lazy-loaded instance
_mention_poller: Optional[MentionPoller] = None


def _get_poller() -> MentionPoller:
    """Get or create the mention poller instance."""
    global _mention_poller
    if _mention_poller is None:
        _mention_poller = MentionPoller()
    return _mention_poller


async def start_polling():
    """Start the mention poller."""
    await _get_poller().start()


async def stop_polling():
    """Stop the mention poller."""
    await _get_poller().stop()


def is_enabled() -> bool:
    """Check if mention polling is enabled."""
    return config.get("polling.enabled", False)
