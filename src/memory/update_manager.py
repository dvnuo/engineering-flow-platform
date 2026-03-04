"""Memory Update Manager for automatic daily memory management."""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.memory.event_log import EventLogger
from src.memory.validators import validate_memory_ops, sanitize_memory_ops

logger = logging.getLogger(__name__)


# Default config
DEFAULT_CONFIG = {
    "max_memory_inject_chars": 2500,
    "max_retrieval_chunks": 5,
    "max_daily_inject_days": 2,
    "max_daily_inject_chars_per_day": 1500,
    "enable_auto_memory": True,
}


class MemoryUpdateManager:
    """Manages automatic memory updates from conversation turns."""

    def __init__(
        self,
        workspace: str,
        llm_client=None,
        memory_system=None,
        config: Optional[Dict[str, Any]] = None,
    ):
        """Initialize memory update manager.
        
        Args:
            workspace: Path to workspace directory
            llm_client: LLM client for generating memory ops
            memory_system: MemorySystem instance for indexing
            config: Configuration dictionary
        """
        self.workspace = Path(workspace)
        self.llm_client = llm_client
        self.memory_system = memory_system
        self.config = {**DEFAULT_CONFIG, **(config or {})}
        self.event_logger = EventLogger(str(self.workspace))

        # Ensure memory directory exists
        self.memory_dir = self.workspace / "memory"
        self.memory_dir.mkdir(exist_ok=True)

    async def on_turn_completed(
        self,
        session_id: str,
        turn_id: int,
        user_text: str,
        assistant_text: str,
        tool_calls: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        """Handle turn completion - generate and apply memory ops.
        
        Args:
            session_id: Session identifier
            turn_id: Turn number
            user_text: User's message
            assistant_text: Assistant's response
            tool_calls: List of tool calls made in this turn
        """
        if not self.config.get("enable_auto_memory", True):
            return

        try:
            # Generate memory ops from turn context
            ops = await self._generate_memory_ops(
                session_id, turn_id, user_text, assistant_text, tool_calls
            )

            # Validate and sanitize ops
            is_valid, error_msg = validate_memory_ops(ops)
            if not is_valid:
                logger.warning(f"Invalid memory ops: {error_msg}")
                self._log_error_event(session_id, turn_id, error_msg)
                return

            ops = sanitize_memory_ops(ops)

            # Apply ops to daily note
            if ops and ops[0].get("op") != "NOOP":
                await self._apply_ops(session_id, turn_id, ops)
                # Refresh index to make new content searchable
                await self._refresh_memory_index()

        except Exception as e:
            logger.error(f"Error in on_turn_completed: {e}")

    async def _generate_memory_ops(
        self,
        session_id: str,
        turn_id: int,
        user_text: str,
        assistant_text: str,
        tool_calls: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        """Generate memory operations using LLM.
        
        Args:
            session_id: Session identifier
            turn_id: Turn number
            user_text: User's message
            assistant_text: Assistant's response
            tool_calls: Tool calls made in this turn
            
        Returns:
            List of memory operations
        """
        if not self.llm_client:
            logger.debug("No LLM client, skipping memory ops generation")
            return [{"op": "NOOP"}]

        # Build context for LLM
        context = self._build_turn_context(
            session_id, turn_id, user_text, assistant_text, tool_calls
        )

        # Call LLM to generate ops
        prompt = self._build_memory_ops_prompt(context)

        try:
            response = await self.llm_client.chat(
                messages=[{"role": "user", "content": prompt}],
                system_prompt="You are a memory analysis assistant. Output ONLY valid JSON.",
            )

            # Parse response
            content = response.get("content", "")
            # Extract JSON from response
            json_str = self._extract_json(content)
            if json_str:
                data = json.loads(json_str)
                return data.get("ops", [{"op": "NOOP"}])
            else:
                logger.warning("Could not parse memory ops from LLM response")
                return [{"op": "NOOP"}]

        except Exception as e:
            logger.error(f"Error generating memory ops: {e}")
            return [{"op": "NOOP"}]

    def _build_turn_context(
        self,
        session_id: str,
        turn_id: int,
        user_text: str,
        assistant_text: str,
        tool_calls: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        """Build context string from turn data."""
        parts = [f"## Turn {turn_id}"]

        parts.append(f"\n### User\n{user_text}")

        parts.append(f"\n### Assistant\n{assistant_text}")

        if tool_calls:
            parts.append("\n### Tool Calls")
            for tc in tool_calls:
                name = tc.get("function", {}).get("name", "unknown")
                args = tc.get("function", {}).get("arguments", {})
                parts.append(f"- {name}: {args}")

        return "\n".join(parts)

    def _build_memory_ops_prompt(self, context: str) -> str:
        """Build prompt for generating memory operations."""
        return f"""You are a memory analysis assistant. Analyze the conversation turn below and extract information worth remembering.

Extract ONLY facts, decisions, and preferences that are explicitly stated. Do NOT infer or add information not present in the conversation.

If nothing worth remembering, return: {{"ops": [{{"op": "NOOP"}}]}}

Output format (JSON only, no markdown):
{{
  "ops": [
    {{
      "op": "ADD|UPDATE|MERGE|DELETE|NOOP",
      "type": "summary|decision|fact|preference",
      "content": "...",
      "confidence": 0.0-1.0,
      "tags": ["tag1", "tag2"]
    }}
  ]
}}

{context}

Remember: Only extract what was explicitly said. Do not invent information.
"""

    def _extract_json(self, text: str) -> Optional[str]:
        """Extract JSON from text that may contain markdown."""
        # Try to find JSON block
        text = text.strip()
        if text.startswith("```"):
            # Extract from code block
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                return text[start:end]
        elif text.startswith("{"):
            end = text.rfind("}") + 1
            if end > 0:
                return text[:end]
        return None

    async def _apply_ops(
        self, session_id: str, turn_id: int, ops: List[Dict[str, Any]]
    ) -> None:
        """Apply memory operations to daily note.
        
        Args:
            session_id: Session identifier
            turn_id: Turn number
            ops: List of memory operations
        """
        if not ops or ops[0].get("op") == "NOOP":
            return

        # Get today's daily note path
        today = datetime.now().strftime("%Y-%m-%d")
        daily_note_path = self.memory_dir / f"{today}.md"

        # Build entry
        timestamp = datetime.utcnow().isoformat() + "Z"
        entries = [f"\n## Turn {turn_id} ({timestamp}) [session {session_id}]"]

        for op in ops:
            if op.get("op") == "NOOP":
                continue

            mem_type = op.get("type", "summary")
            content = op.get("content", "")
            confidence = op.get("confidence", 0.5)
            tags = op.get("tags", [])

            tag_str = f"|tags={','.join(tags)}" if tags else ""
            entries.append(
                f"- [{mem_type}|c={confidence:.1f}{tag_str}] {content}"
            )

        entry_text = "\n".join(entries)

        # Append to daily note
        with open(daily_note_path, "a") as f:
            f.write(entry_text + "\n")

        logger.info(f"Wrote {len(ops)} memory ops to {daily_note_path}")

    async def _refresh_memory_index(self) -> None:
        """Refresh memory index to make new content searchable."""
        if self.memory_system:
            try:
                self.memory_system.refresh_index_if_needed()
            except Exception as e:
                logger.error(f"Error refreshing memory index: {e}")

    def _log_error_event(
        self, session_id: str, turn_id: int, error_msg: str
    ) -> None:
        """Log an error event."""
        self.event_logger.log_event(
            session_id,
            turn_id,
            "error",
            error_msg,
        )
