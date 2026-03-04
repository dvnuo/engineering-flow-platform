"""Long-term memory generator from daily memories."""

import json
import logging
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

SYSTEM = "You are a memory consolidation assistant. Output ONLY markdown."


def _build_consolidation_prompt(current_memory: str, daily_reports: List[Dict[str, str]]) -> str:
    """Build prompt for consolidating memory.
    
    Args:
        current_memory: Current MEMORY.md content
        daily_reports: List of {day: filename, content: md_content}
    """
    lines = [
        "# Memory Consolidation",
        "",
        "You are to produce a NEW version of MEMORY.md by consolidating:",
        "1. The current MEMORY.md (existing long-term memories)",
        "2. Recent daily reports (new information from the last 2 days)",
        "",
        "## Rules:",
        "- Keep stable, durable information from current MEMORY.md",
        "- Add new information from daily reports that should be remembered long-term",
        "- Remove outdated or contradicted information",
        "- Merge duplicate or similar entries",
        "- Do NOT include: assistant actions (saved/remembered), trivial interactions",
        "- Output format: markdown with ## sections",
        "",
        "## Current MEMORY.md:",
        current_memory[:5000] if current_memory else "(empty)",
        "",
        "## Recent Daily Reports:",
    ]
    
    for d in daily_reports:
        lines.append(f"\n### {d['day']}")
        lines.append(d['content'][:3000])
    
    lines.extend([
        "",
        "## Output:",
        "Produce the NEW MEMORY.md content (full replacement).",
    ])
    
    return "\n".join(lines)


async def update_long_term_memory_from_daily(
    workspace: str,
    llm_client,
    *,
    daily_paths: List[str]
) -> str:
    """Update long-term MEMORY.md by consolidating with recent dailies.
    
    Args:
        workspace: Path to workspace
        llm_client: LLM client
        daily_paths: List of daily memory file paths (will use last 2)
        
    Returns:
        Path to updated MEMORY.md
    """
    ws = Path(workspace)
    mem_path = ws / "MEMORY.md"
    memory_dir = ws / "memory"
    
    # Read current MEMORY.md
    current_memory = ""
    if mem_path.exists():
        current_memory = mem_path.read_text(encoding="utf-8")
    
    # Get recent 2 days of daily reports
    daily_reports = []
    today = date.today()
    
    # Sort daily paths by date (newest first)
    daily_files = sorted(
        [p for p in daily_paths if Path(p).exists()],
        key=lambda p: Path(p).stem,
        reverse=True
    )[:2]  # Last 2 days
    
    for p in daily_files:
        day = Path(p).stem
        content = Path(p).read_text(encoding="utf-8")
        daily_reports.append({"day": day, "content": content})
    
    if not daily_reports:
        logger.info("No daily reports to consolidate")
        return str(mem_path)
    
    # Call LLM to consolidate
    prompt = _build_consolidation_prompt(current_memory, daily_reports)
    
    try:
        resp = await llm_client.chat(
            messages=[{"role": "user", "content": prompt}],
            system_prompt=SYSTEM,
        )
        new_memory = (resp.get("content") or "").strip()
        
        if new_memory:
            # Ensure it starts with # MEMORY
            if not new_memory.startswith("#"):
                new_memory = "# MEMORY\n\n" + new_memory
            
            # Atomic write
            temp_path = mem_path.with_suffix(".md.tmp")
            temp_path.write_text(new_memory + "\n", encoding="utf-8")
            temp_path.replace(mem_path)
            
            logger.info(f"Consolidated MEMORY.md with {len(daily_reports)} daily reports")
        else:
            logger.warning("LLM returned empty memory content")
            
    except Exception as e:
        logger.error(f"Failed to consolidate memory: {e}")
    
    return str(mem_path)
