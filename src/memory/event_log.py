"""Raw Event Log for memory tracking.

Logs all conversation events to JSONL files for traceability and replay.
"""
from datetime import date, datetime, timedelta
from pathlib import Path

import json
from typing import Any, Dict, Optional


class EventLogger:
    """Logs conversation events to JSONL files."""

    def __init__(self, workspace: str):
        """Initialize event logger.
        
        Args:
            workspace: Path to workspace directory
        """
        self.workspace = Path(workspace)
        self.sessions_dir = self.workspace / ".sessions"
        self.sessions_dir.mkdir(exist_ok=True)
        
        # Also check legacy sessions directory
        self.legacy_sessions_dir = self.workspace / "sessions"

    def _get_session_log_path(self, session_id: str) -> Path:
        """Get the log file path for a session."""
        return self.sessions_dir / f"{session_id}.jsonl"

    def log_event(
        self,
        session_id: str,
        turn_id: int,
        event_type: str,
        content: str,
        tool_name: Optional[str] = None,
        tool_args: Optional[Dict[str, Any]] = None,
        tool_result: Optional[str] = None,
    ) -> None:
        """Log a single event to the session's JSONL file.
        
        Args:
            session_id: Session identifier
            turn_id: Turn number within the session
            event_type: Type of event (user|assistant|tool)
            content: Event content
            tool_name: Name of tool (for tool events)
            tool_args: Tool arguments (for tool events)
            tool_result: Tool result (for tool events)
        """
        event = {
            "ts": datetime.utcnow().isoformat() + "Z",
            "session_id": session_id,
            "turn_id": turn_id,
            "type": event_type,
            "content": content,
            "tool_name": tool_name,
            "tool_args": tool_args,
            "tool_result": tool_result,
            "workspace": str(self.workspace),
        }

        log_path = self._get_session_log_path(session_id)
        with open(log_path, "a") as f:
            f.write(json.dumps(event) + "\n")

    def log_user_message(self, session_id: str, turn_id: int, message: str) -> None:
        """Log user message."""
        self.log_event(session_id, turn_id, "user", message)

    def log_assistant_message(
        self, session_id: str, turn_id: int, message: str
    ) -> None:
        """Log assistant message."""
        self.log_event(session_id, turn_id, "assistant", message)

    def log_tool_call(
        self,
        session_id: str,
        turn_id: int,
        tool_name: str,
        tool_args: Dict[str, Any],
        tool_result: str,
    ) -> None:
        """Log tool call and result."""
        self.log_event(
            session_id,
            turn_id,
            "tool",
            f"Called {tool_name}",
            tool_name=tool_name,
            tool_args=tool_args,
            tool_result=tool_result,
        )

    def get_session_events(self, session_id: str) -> list:
        """Get all events for a session.
        
        Args:
            session_id: Session identifier
            
        Returns:
            List of event dictionaries
        """
        log_path = self._get_session_log_path(session_id)
        if not log_path.exists():
            return []

        events = []
        with open(log_path, "r") as f:
            for line in f:
                line = line.strip()
                if line:
                    events.append(json.loads(line))
        return events

    def get_turn_events(self, session_id: str, turn_id: int) -> list:
        """Get events for a specific turn.
        
        Args:
            session_id: Session identifier
            turn_id: Turn number
            
        Returns:
            List of event dictionaries for the turn
        """
        all_events = self.get_session_events(session_id)
        return [e for e in all_events if e.get("turn_id") == turn_id]
    def list_sessions(self) -> list:
        """List all session IDs that have event logs."""
        return [p.stem for p in self.sessions_dir.glob("*.jsonl")]

    def iter_all_events(self):
        """Iterate over all events from all session logs."""
        for p in sorted(self.sessions_dir.glob("*.jsonl")):
            with open(p, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        yield json.loads(line)

    def get_events_grouped_by_day(self, max_days_back: int = 30) -> dict:
        """Get events grouped by day (YYYY-MM-DD), including legacy sessions.
        
        Each event is tagged with its session_id for proper attribution.
        Only returns events from the last max_days_back days.
        """
        # Calculate cutoff date
        today = date.today()
        cutoff = today - timedelta(days=max_days_back)
        
        groups = {}
        
        # Read from current .sessions/ directory
        for e in self.iter_all_events():
            ts = e.get("ts", "")
            day = ts[:10] if len(ts) >= 10 else "unknown"
            groups.setdefault(day, []).append(e)
        
        # Also read from legacy sessions/ directory
        if self.legacy_sessions_dir.exists():
            for p in sorted(self.legacy_sessions_dir.glob("*.jsonl")):
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                    messages = data.get("messages", [])
                    for i, msg in enumerate(messages):
                        ts = msg.get("timestamp", "")
                        day = ts[:10] if len(ts) >= 10 else "unknown"
                        if day != "unknown":
                            # Skip events older than max_days_back
                            try:
                                day_date = datetime.strptime(day, "%Y-%m-%d").date()
                                if day_date < cutoff:
                                    continue
                            except ValueError:
                                pass
                            
                            # Use turn_id as message index for ordering
                            groups.setdefault(day, []).append({
                                "ts": ts,
                                "session_id": data.get("session_id", p.stem),
                                "turn_id": i,
                                "type": msg.get("role", "message"),
                                "content": msg.get("content", ""),
                            })
                except Exception:
                    pass
        
        return groups
