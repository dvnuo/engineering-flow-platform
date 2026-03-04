"""Daily memory generator from session events."""

import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.memory.event_log import EventLogger

logger = logging.getLogger(__name__)

DAILY_TEMPLATE_SYSTEM = "You are a technical daily report generator. Output ONLY markdown."


def _build_daily_prompt(day: str, events: List[Dict[str, Any]]) -> str:
    """Build prompt for generating daily report from events."""
    lines = [
        f"# {day}",
        "",
        "You will generate a concise engineering daily report from these events.",
        "Rules:",
        "- Focus on completed work, code/config changes, tests, tool outputs, decisions, risks.",
        "- Do NOT write 'assistant saved/remembered'.",
        "- If there is no meaningful engineering work, write a short 'Notes' only.",
        "- Use this structure if possible:",
        " ## <Topic> (Completed/In Progress)",
        " ### Changes Made",
        " ### Testing Results",
        " ### Notes",
        " ### Git Status",
        "",
        "Events:",
    ]
    
    # Limit to most recent 200 events to avoid huge prompts
    for e in events[-200:]:
        t = e.get("type")
        sid = e.get("session_id", "unknown")
        tid = e.get("turn_id", 0)
        content = (e.get("content") or "")[:400]
        
        if t == "tool":
            tool_name = e.get("tool_name", "unknown")
            tool_args = json.dumps(e.get("tool_args") or {})[:200]
            tool_res = (e.get("tool_result") or "")[:400]
            lines.append(f"- [{t}] s={sid} turn={tid} tool={tool_name} args={tool_args} result={tool_res}")
        else:
            lines.append(f"- [{t}] s={sid} turn={tid} {content}")
    
    return " ".join(lines)


async def ensure_daily_memories(
    workspace: str,
    llm_client=None,
    *,
    backfill_only_missing: bool = True
) -> List[str]:
    """Ensure daily memory files exist, optionally backfilling from session events.
    
    Args:
        workspace: Path to workspace directory
        llm_client: Optional LLM client for generating daily reports
        backfill_only_missing: If True, only create missing daily files
        
    Returns:
        List of created daily file paths
    """
    ws = Path(workspace)
    mem_dir = ws / "memory"
    mem_dir.mkdir(exist_ok=True)
    
    event_logger = EventLogger(str(ws))
    groups = event_logger.get_events_grouped_by_day()
    
    # Only backfill historical days (not today)
    today = date.today()
    
    created = []
    for day, events in sorted(groups.items()):
        if day == "unknown":
            continue
        
        # Skip today - only backfill historical days
        try:
            day_date = datetime.strptime(day, "%Y-%m-%d").date()
            if day_date >= today:
                continue
        except ValueError:
            continue
            
        path = mem_dir / f"{day}.md"
        
        if backfill_only_missing and path.exists():
            continue
            
        # Skip if no meaningful events (less than 3)
        if len(events) < 3:
            logger.debug(f"Skipping {day}: only {len(events)} events")
            continue
            
        if llm_client:
            # Generate daily report using LLM
            prompt = _build_daily_prompt(day, events)
            try:
                resp = await llm_client.chat(
                    messages=[{"role": "user", "content": prompt}],
                    system_prompt=DAILY_TEMPLATE_SYSTEM,
                )
                md = (resp.get("content") or "").strip()
                if not md.startswith("#"):
                    md = f"# {day}\n\n" + md
            except Exception as e:
                logger.error(f"Failed to generate daily for {day}: {e}")
                md = f"# {day}\n\n(No events summary available)"
        else:
            # Simple fallback without LLM
            md = f"# {day}\n\n"
            for e in events[-20:]:
                t = e.get("type", "unknown")
                content = (e.get("content") or "")[:200]
                md += f"- [{t}] {content}\n"
        
        path.write_text(md + "\n", encoding="utf-8")
        created.append(str(path))
        logger.info(f"Created daily memory: {path}")
    
    return created
