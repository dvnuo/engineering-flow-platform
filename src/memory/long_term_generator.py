"""Long-term memory generator from daily memories."""

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

SYSTEM = "You are a memory extraction assistant. Output ONLY JSON."


def _extract_prompt(day_md: str) -> str:
    """Build prompt for extracting long-term memories from daily report."""
    return f"""Extract stable long-term memories from the daily report below.

Rules:
- Only include stable info: user profile facts, durable preferences, project decisions, rules.
- Do NOT include assistant actions (saved/remembered).
- If conflicting/uncertain, skip.
- Output JSON only: {{ "items": [ {{ "category": "facts|preferences|decisions|rules", "key": "user.preferred_name", "value": "Lucas", "confidence": 0.0-1.0 }} ] }}

Daily report:
{day_md[:8000]}
""".strip()


def _upsert_memory_md(memory_md: str, items: List[Dict[str, Any]]) -> str:
    """Update MEMORY.md with new items (key-based upsert)."""
    lines = memory_md.splitlines()
    existing_keys = {}
    other_lines = []
    
    # Parse existing key lines
    key_re = re.compile(r"^- key:\s*(?P<key>[^|]+)\s*\|\s*value:\s*(?P<value>.+)$")
    for line in lines:
        m = key_re.match(line.strip())
        if m:
            key = m.group("key").strip()
            existing_keys[key] = line
        else:
            other_lines.append(line)
    
    # Categorize new items
    by_cat = {"facts": [], "preferences": [], "decisions": [], "rules": []}
    for it in items:
        cat = it.get("category", "").lower()
        key = (it.get("key") or "").strip()
        val = (it.get("value") or "").strip()
        c = float(it.get("confidence") or 0.5)
        
        if not cat or not key or not val:
            continue
        if cat not in by_cat:
            cat = "facts"  # Default
        by_cat[cat].append((key, val, c))
    
    # Rebuild output
    output = ["# MEMORY", ""]
    
    for title, cat in [("Structured Facts", "facts"), ("Preferences", "preferences"), 
                        ("Decisions", "decisions"), ("Rules", "rules")]:
        output.append(f"## {title}")
        
        # First add existing lines for this category (that aren't being overwritten)
        for line in other_lines:
            if f"## {title}" not in line:
                output.append(line)
        
        # Then add new items
        for key, val, c in by_cat[cat]:
            if key in existing_keys:
                # Skip - already exists
                continue
            output.append(f"- key: {key} | value: {val} | c={c:.2f}")
        
        output.append("")
    
    return "\n".join(output).strip() + "\n"


async def update_long_term_memory_from_daily(
    workspace: str,
    llm_client,
    *,
    daily_paths: List[str]
) -> str:
    """Update long-term MEMORY.md from daily reports.
    
    Args:
        workspace: Path to workspace
        llm_client: LLM client for extraction
        daily_paths: List of daily memory file paths
        
    Returns:
        Path to updated MEMORY.md
    """
    ws = Path(workspace)
    mem_path = ws / "MEMORY.md"
    
    current = mem_path.read_text(encoding="utf-8") if mem_path.exists() else "# MEMORY\n"
    
    all_items: List[Dict[str, Any]] = []
    
    for p in daily_paths:
        try:
            md = Path(p).read_text(encoding="utf-8")
            prompt = _extract_prompt(md)
            resp = await llm_client.chat(
                messages=[{"role": "user", "content": prompt}],
                system_prompt=SYSTEM,
            )
            content = resp.get("content", "{}")
            # Try to extract JSON from response
            try:
                # Handle markdown code blocks
                if "```" in content:
                    start = content.find("```json")
                    if start == -1:
                        start = content.find("```")
                    end = content.rfind("```")
                    if start >= 0 and end > start:
                        content = content[start+7 if "json" in content[start:start+10] else start+3:end]
                data = json.loads(content.strip())
                all_items.extend(data.get("items", []))
            except json.JSONDecodeError:
                logger.warning(f"Failed to parse JSON from LLM response for {p}")
        except Exception as e:
            logger.error(f"Failed to process daily {p}: {e}")
    
    if all_items:
        new_md = _upsert_memory_md(current, all_items)
        mem_path.write_text(new_md, encoding="utf-8")
        logger.info(f"Updated MEMORY.md with {len(all_items)} items")
    
    return str(mem_path)
