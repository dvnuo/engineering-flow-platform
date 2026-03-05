"""Daily memory generator from session events."""

import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.memory.event_log import EventLogger

logger = logging.getLogger(__name__)

DAILY_TEMPLATE_SYSTEM = """You are generating a concise engineering daily report from raw session events.

STRICT RULES:
- Use ONLY information present in the events. Do NOT infer or invent.
- Do NOT write "assistant saved/remembered/decided". Those are NOT memories.
- Prefer tool evidence (git, test outputs) over conversational statements.
- De-duplicate repeated items.
- Keep the report short and clean.
- Output format: Markdown with ## sections only.

OUTPUT STRUCTURE:
# <DATE>
## <Topic 1> (Completed|In Progress)
### Changes
- ...
### Testing
- ...
### Notes
- ...
## <Topic 2> ...
(Max 5 topics)"""
MERGE_TEMPLATE_SYSTEM = "You are a technical daily report merger. Output ONLY markdown."


def _build_partial_prompt(day: str, events: List[Dict[str, Any]], chunk_idx: int, total_chunks: int) -> str:
    """Build prompt for generating partial summary from a chunk of events."""
    # Extract event summary for LLM
    event_summary = []
    for e in events:
        t = e.get("type")
        sid = e.get("session_id", "?")
        
        if t == "tool":
            tool_name = e.get("tool_name", "unknown")
            # Use tool_result (from EventLogger) with fallback to result
            tool_output = e.get("tool_result") or e.get("result", "")
            result_preview = (tool_output or "")[:80]
            event_summary.append(f"[tool] {tool_name}: {result_preview}")
        elif t == "user":
            msg = (e.get("content", "") or "")[:80]
            event_summary.append(f"[user] {msg}")
        elif t == "assistant":
            msg = (e.get("content", "") or "")[:80]
            event_summary.append(f"[assistant] {msg}")
    
    events_text = "\n".join(f"- {e}" for e in event_summary)
    
    prompt = f"""## Part {chunk_idx + 1}/{total_chunks}

Generate a structured summary from these events. Focus on:
- Code changes (git commit, branch, file edits)
- Test results and debugging
- Technical decisions and implementations
- Errors and resolutions

Events:
{events_text}

Output format:
## <Topic Name> (Completed|In Progress)
### Changes
### Testing
### Notes

Keep it concise - max 3 bullet points per section."""
    return prompt


def _build_merge_prompt(day: str, partial_summaries: List[str]) -> str:
    """Build prompt for merging partial summaries into final daily report."""
    summaries_text = "\n\n".join(
        f"### Part {i + 1}\n{s}" 
        for i, s in enumerate(partial_summaries)
    )
    
    prompt = f"""# {day}

Merge these partial summaries into a single daily report.

STRICT RULES:
- Use ONLY the information from the summaries
- Remove duplicates and merge related items
- Do NOT include trivial conversations or "assistant" actions
- Focus on: code changes, tests, deployments, bugs fixed, features added
- Max 5 topics total
- Output ONLY markdown

{summaries_text}

Output:
# {day}
## <Topic 1> (Completed|In Progress)
### Changes
### Testing
### Notes
## <Topic 2>..."""
    return prompt


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
    
    # CHUNK_SIZE: number of events per partial summary
    CHUNK_SIZE = 150
    
    created = []
    for day, events in sorted(groups.items()):
        if day == "unknown":
            continue
        
        # Process all days including today
            
        path = mem_dir / f"{day}.md"
        
        # Always regenerate for today, skip only historical if backfill_only_missing
        if backfill_only_missing and path.exists() and day != today.strftime("%Y-%m-%d"):
            continue
            
        # Skip if no meaningful events
        if len(events) < 3:
            logger.debug(f"Skipping {day}: only {len(events)} events")
            continue
            
        if llm_client:
            # Split events into chunks
            chunks = []
            for i in range(0, len(events), CHUNK_SIZE):
                chunks.append(events[i:i + CHUNK_SIZE])
            
            logger.info(f"Generating daily for {day}: {len(events)} events split into {len(chunks)} chunks")
            
            # Generate partial summaries for each chunk
            partial_summaries = []
            for chunk_idx, chunk in enumerate(chunks):
                prompt = _build_partial_prompt(day, chunk, chunk_idx, len(chunks))
                
                try:
                    resp = await llm_client.chat(
                        messages=[{"role": "user", "content": prompt}],
                        system_prompt=DAILY_TEMPLATE_SYSTEM,
                    )
                    summary = (resp.get("content") or "").strip()
                    if summary:
                        partial_summaries.append(summary)
                except Exception as e:
                    logger.error(f"Failed to generate partial for {day} chunk {chunk_idx}: {e}")
            
            # Merge all partial summaries
            if len(partial_summaries) == 1:
                md = f"# {day}\n\n{partial_summaries[0]}"
            elif len(partial_summaries) > 1:
                merge_prompt = _build_merge_prompt(day, partial_summaries)
                try:
                    resp = await llm_client.chat(
                        messages=[{"role": "user", "content": merge_prompt}],
                        system_prompt=MERGE_TEMPLATE_SYSTEM,
                    )
                    md = (resp.get("content") or "").strip()
                    if not md.startswith("#"):
                        md = f"# {day}\n\n{md}"
                except Exception as e:
                    logger.error(f"Failed to merge summaries for {day}: {e}")
                    md = f"# {day}\n\n" + "\n\n".join(partial_summaries)
            else:
                md = f"# {day}\n\n(No summary available)"
        else:
            # Simple fallback without LLM
            md = f"# {day}\n\n"
            for e in events[:50]:
                t = e.get("type", "unknown")
                content = (e.get("content") or "")[:100]
                md += f"- [{t}] {content}\n"
        
        path.write_text(md + "\n", encoding="utf-8")
        created.append(str(path))
        logger.info(f"Created daily memory: {path}")
    
    return created
