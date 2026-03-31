"""Lightweight skill-mode session helpers."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from src.agents.llm import _normalize_provider_key, llm_client
from src.config import config
from src.skills.registry import Skill

logger = logging.getLogger(__name__)


def _load_skill_references(skill_path: str) -> str:
    """Load all reference files from skill's references folder or skill directory.
    
    Supports two patterns:
    1. references/ folder: skill_path/references/*.md (preferred)
    2. Root-level refs: skill_path/ref-*.md or skill_path/*.md (excluding skill.md)
    
    Args:
        skill_path: Path to the skill directory
        
    Returns:
        List of available reference files with their names for tool use
    """
    if not skill_path:
        return "(no skill path)"
    
    skill_dir = Path(skill_path)
    ref_files = []
    
    # Pattern 1: references/ folder (preferred)
    references_dir = skill_dir / "references"
    if references_dir.exists():
        for ref_file in sorted(references_dir.glob("*")):
            if ref_file.is_file() and ref_file.suffix in (".md", ".txt", ".html"):
                ref_files.append(str(ref_file))
    else:
        # Pattern 2: Root-level reference files (ref-*.md or any *.md except skill.md/SKILL.md)
        for ref_file in sorted(skill_dir.glob("*.md")):
            if ref_file.name.lower() not in ("skill.md",):  # Skip skill definition file
                ref_files.append(str(ref_file))
    
    if not ref_files:
        return "(no reference files found)"
    
    # Return just the list of files, not content
    return "Available references:\n" + "\n".join(f"- {Path(f).name}: {f}" for f in ref_files)


def _list_skill_scripts(skill_path: str) -> str:
    """List available Python scripts in skill's scripts folder.
    
    Args:
        skill_path: Path to the skill directory
        
    Returns:
        List of available scripts with their paths
    """
    if not skill_path:
        return "(no skill path)"
    
    skill_dir = Path(skill_path)
    scripts_dir = skill_dir / "scripts"
    
    if not scripts_dir.exists():
        return "(no scripts folder)"
    
    scripts = []
    for script_file in sorted(scripts_dir.glob("*.py")):
        if script_file.is_file():
            scripts.append(f"- {script_file.name}: {script_file}")
    
    if not scripts:
        return "(no Python scripts found)"
    
    return "Available scripts:\n" + "\n".join(scripts) + "\n\nYou can read these scripts using cat tool, or execute them using run_command with: run_command(cmd='python3', args=['path/to/script.py', ...])"


# Token estimation: ~1 token ≈ 4 characters for English, ~2 characters for Chinese
# Use conservative estimate: 3 characters per token
CHARS_PER_TOKEN = 3

# Max context tokens for skill mode (leave room for response)
# Updated to 64K context window
MAX_CONTEXT_TOKENS = 264000
# Reserve tokens for system prompt + response (20% for response)
CONTEXT_RESERVED_TOKENS = 64000


def estimate_tokens(text: str) -> int:
    """Estimate token count for text."""
    if not text:
        return 0
    return len(text) // CHARS_PER_TOKEN


def get_available_context_tokens() -> int:
    """Get available tokens for user messages in skill mode."""
    return MAX_CONTEXT_TOKENS - CONTEXT_RESERVED_TOKENS


@dataclass
class SkillSession:
    """Minimal state for an ongoing skill-mode conversation."""

    skill_name: str
    original_user_request: str
    status: str = "active"  # active / waiting_user / finished
    goal: str = ""
    plan: List[Dict[str, str]] = field(default_factory=list)
    completed_steps: List[Dict[str, Any]] = field(default_factory=list)
    memory_summary: str = ""
    artifacts: Dict[str, Any] = field(default_factory=dict)
    pending_question: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SkillSession":
        return cls(
            skill_name=data.get("skill_name", ""),
            original_user_request=data.get("original_user_request", ""),
            status=data.get("status", "active"),
            goal=data.get("goal", ""),
            plan=data.get("plan", []) or [],
            completed_steps=data.get("completed_steps", []) or [],
            memory_summary=data.get("memory_summary", ""),
            artifacts=data.get("artifacts", {}) or {},
            pending_question=data.get("pending_question"),
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _build_skill_plan_system_prompt(skill: Skill) -> str:
    return (
        "You are entering skill mode. Build a SHORT practical plan for the skill.\n"
        f"Skill: {skill.name}\n"
        f"Description: {skill.description}\n\n"
        "Return JSON ONLY with this shape:\n"
        '{"goal":"...","steps":[{"id":"...","type":"execute|user_input_if_needed","title":"..."}]}'
        "\nRules:\n"
        "- If task is simple, output exactly 1 step\n"
        "- If task needs interaction or multiple phases, output 2-5 steps\n"
        "- Allowed step.type: execute, user_input_if_needed\n"
        "- Steps are lightweight references for later turns, not a strict workflow\n"
        "- Do not over-split into tiny steps\n"
        "- Do not output execution_style or any other control fields\n"
        "- Do not add extra keys\n"
        "- Do not wrap JSON in markdown\n"
    )


def _build_skill_plan_user_prompt(skill: Skill, user_message: str) -> str:
    strategy_hint = "\n".join(f"- {s}" for s in skill.strategy) if skill.strategy else "(none)"
    return (
        f"User request: {user_message}\n"
        f"Skill strategy hints:\n{strategy_hint}\n"
        "Generate the initial lightweight plan now."
    )


def _build_skill_mode_system_prompt(skill: Skill, skill_session: SkillSession) -> str:
    # Calculate available tokens for completed steps
    available_tokens = get_available_context_tokens()
    # Estimate ~100 chars per step entry, so ~10 tokens per step
    tokens_per_step = 100 // CHARS_PER_TOKEN
    max_steps = min(len(skill_session.completed_steps), available_tokens // tokens_per_step)
    max_steps = max(max_steps, 100)  # At least 100 steps
    
    completed = "\n".join(
        f"- {item.get('result', '')}" for item in skill_session.completed_steps[-max_steps:] if item.get("result")
    ) or "(none)"
    plan = "\n".join(
        f"- [{step.get('type', 'execute')}] {step.get('title', '')}" for step in skill_session.plan
    ) or "(none)"

    # Include skill strategy for ongoing sessions
    strategy_hint = "\n".join(f"- {s}" for s in skill.strategy) if skill.strategy else "(none)"
    artifacts_summary = _build_artifacts_summary(skill_session)
    
    # Load skill references
    references = _load_skill_references(getattr(skill, 'path', '') or '')
    skill_scripts = _list_skill_scripts(getattr(skill, 'path', '') or '')

    return (
        "You are running an active skill-mode session.\n"
        f"Skill: {skill.name}\n"
        f"Goal: {skill_session.goal or skill.description}\n"
        f"Plan:\n{plan}\n\n"
        f"Completed summary:\n{completed}\n\n"
        f"Memory summary:\n{skill_session.memory_summary or '(empty)'}\n\n"
        f"Known artifacts:\n{artifacts_summary}\n\n"
        f"Strategy hints:\n{strategy_hint}\n\n"
        f"{references}\n\n"
        f"{skill_scripts}\n\n"
        "Available tools:\n"
        "- run_command(cmd, args): Execute shell command. Examples:\n"
        "  run_command(cmd='cat', args=['/path/to/ref-01.md'])  # Read a file\n"
        "  run_command(cmd='python3', args=['/path/to/script.py', '--arg', 'value'])  # Run script\n"
        "- discover_commands(prefix, contains): Find available commands\n\n"
        "Output rules (STRICT):\n"
        "1) First line MUST be exactly one marker: [EXECUTE] or [ASK_USER] or [FINISH]\n"
        "2) No other prefix before first line\n"
        "3) Plain text: no markdown wrappers needed\n"
        "4) Code blocks: ALWAYS use triple backticks with language hint (```python, ```javascript, etc.)\n"
        "5) Advance only ONE small step this turn\n"
        "6) If key missing info blocks progress -> [ASK_USER] and ask ONE minimal necessary question with brief reason\n"
        "7) If enough info and task can progress -> [EXECUTE]\n"
        "8) If goal is done -> [FINISH] with concise final summary\n"
        "9) Tools should only be used when they clearly help progress the current skill\n"
        "10) Do not call tools speculatively\n"
        "11) If key user information is missing, ask user instead of over-searching with tools\n"
        "12) If you create or update a file, always show the complete code in a markdown code block\n"
    )


def _build_skill_mode_user_prompt(user_message: str, skill_session: SkillSession) -> str:
    waiting = "yes" if skill_session.status == "waiting_user" else "no"
    return (
        f"Latest user input: {user_message}\n"
        f"Was waiting for user input: {waiting}\n"
        f"Pending question: {skill_session.pending_question or '(none)'}"
    )


def _safe_json_loads(text: str) -> Dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end > start:
        cleaned = cleaned[start:end + 1]
    return json.loads(cleaned)


def _normalize_plan(raw: Dict[str, Any], fallback_goal: str) -> Tuple[str, List[Dict[str, str]]]:
    goal = str(raw.get("goal") or fallback_goal).strip()
    steps = raw.get("steps") or []
    normalized_steps: List[Dict[str, str]] = []
    for idx, step in enumerate(steps[:5], 1):
        if not isinstance(step, dict):
            continue
        step_type = step.get("type", "execute")
        if step_type not in ("execute", "user_input_if_needed"):
            step_type = "execute"
        normalized_steps.append(
            {
                "id": str(step.get("id") or f"step_{idx}"),
                "type": step_type,
                "title": str(step.get("title") or f"Step {idx}"),
            }
        )
    if not normalized_steps:
        normalized_steps = [
            {"id": "analyze_request", "type": "execute", "title": "Analyze user request"},
            {"id": "collect_missing_info", "type": "user_input_if_needed", "title": "Ask for missing required input"},
            {"id": "generate_output", "type": "execute", "title": "Generate final output"},
        ]
    return goal, normalized_steps


async def generate_initial_skill_plan(skill: Skill, user_message: str, model: Optional[str] = None, return_usage: bool = False) -> Union[SkillPlanResult, Tuple[str, List[Dict[str, str]]]]:
    """Generate initial skill plan.
    
    Args:
        skill: The skill to generate plan for
        user_message: User's request message
        model: Optional model override
        return_usage: If True, always returns 3-tuple including usage
    
    Returns:
        If return_usage=True: (goal, steps, usage_dict)
        If return_usage=False: (goal, steps) - for backward compatibility
    """
    system_prompt = _build_skill_plan_system_prompt(skill)
    user_prompt = _build_skill_plan_user_prompt(skill, user_message)

    provider = (config.llm.get("provider") or getattr(llm_client, "default_provider", "openai")).lower()
    # Use consistent input format: input_text block for user content
    kwargs = {
        "input_items": [{"role": "user", "content": [{"type": "input_text", "text": user_prompt}]}],
        "system_prompt": system_prompt,
        "tools": [],
        "reasoning_replay": False,
        "provider": _normalize_provider_key(provider),
    }
    if model:
        kwargs["model"] = model

    result = await llm_client.responses(**kwargs)
    content = (result.get("content") or "").strip()

    # Extract usage (always track it internally)
    iter_usage = result.get("usage", {}) or {}
    usage_data = {
        "prompt_tokens": iter_usage.get("prompt_tokens", 0),
        "completion_tokens": iter_usage.get("completion_tokens", 0),
        "total_tokens": iter_usage.get("total_tokens", 0),
    }

    try:
        raw_plan = _safe_json_loads(content)
        goal, steps = _normalize_plan(raw_plan, fallback_goal=skill.description)
    except Exception as exc:
        logger.warning(f"[SkillMode] Failed to parse initial plan JSON, using fallback: {exc}")
        goal, steps = _normalize_plan({}, fallback_goal=skill.description)

    # Always return 3-tuple for consistent type; caller can ignore usage if not needed
    return goal, steps, usage_data


def _parse_skill_control_marker(output: str) -> Tuple[str, str]:
    text = (output or "").strip()
    if not text:
        return "execute", ""

    first_line, _, rest = text.partition("\n")
    marker = first_line.strip().upper()
    body = rest.strip() if rest else ""

    if marker == "[ASK_USER]":
        return "ask_user", body
    if marker == "[FINISH]":
        return "finish", body
    if marker == "[EXECUTE]":
        return "execute", body

    return "execute", text


def _update_skill_memory_summary(skill_session: SkillSession, user_message: str, latest_result: str, max_chars: int = 8000) -> str:
    chunks = []
    if skill_session.memory_summary:
        chunks.append(skill_session.memory_summary)
    if user_message.strip():
        chunks.append(f"User input: {user_message.strip()}")
    if latest_result.strip():
        chunks.append(f"Action/result: {latest_result.strip()}")

    merged = "\n".join(chunks)
    if len(merged) > max_chars:
        merged = merged[-max_chars:]
    return merged


_FILE_PATH_PATTERN = re.compile(
    r"(?<![\w/.-])([A-Za-z0-9_./-]+\.(?:py|js|ts|jsx|tsx|java|cpp|c|go|rb|php|swift|kt|scala|rs|feature|xml|json|ya?ml|md|sh|bash|sql|html|css|yml|yaml|toml|ini|cfg|conf|env))(?![\w.-])",
    re.IGNORECASE,
)
_ISSUE_KEY_PATTERN = re.compile(r"\b[A-Z][A-Z0-9]+-\d+\b")


def _looks_like_file_content(text: str) -> bool:
    """Heuristic check for code/feature/config-like output content."""
    if not text or len(text.strip()) < 10:
        return False

    lowered = text.lower()
    content_signals = (
        "```",
        "def ",
        "class ",
        "import ",
        "from ",
        "public class ",
        "feature:",
        "scenario:",
        "given ",
        "when ",
        "then ",
        "{",
        "}",
        ":",
        "version:",
    )
    if any(signal in lowered for signal in content_signals):
        return True

    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) >= 2 and all(len(line) < 200 for line in lines[:8]):
        return True
    return False


def _extract_skill_artifacts(
    skill_session: SkillSession,
    user_message: str,
    latest_result: str,
    was_waiting_user: bool = False,
) -> Dict[str, Any]:
    """Extract lightweight structured artifacts from current turn."""
    artifacts: Dict[str, Any] = {}
    full_text = f"{user_message}\n{latest_result}"
    files_created: List[str] = []
    files_updated: List[str] = []
    requested_files = [m.group(1) for m in _FILE_PATH_PATTERN.finditer(user_message or "")]
    if requested_files:
        artifacts["requested_files"] = requested_files

    for line in full_text.splitlines():
        line_paths = [m.group(1) for m in _FILE_PATH_PATTERN.finditer(line)]
        if not line_paths:
            continue
        lowered = line.lower()
        if any(token in lowered for token in ("create", "created", "generate", "generated", "new file", "wrote")):
            files_created.extend(line_paths)
        elif any(token in lowered for token in ("update", "updated", "modify", "modified", "edit", "edited", "patch")):
            files_updated.extend(line_paths)
        else:
            # Default to created for explicit file references in execution outputs.
            files_created.extend(line_paths)

    if files_created:
        artifacts["files_created"] = files_created
    if files_updated:
        artifacts["files_updated"] = files_updated

    # Fallback inference: infer target files from user-requested paths when output looks like file content.
    if requested_files and not files_created and not files_updated and _looks_like_file_content(latest_result):
        lowered_user = (user_message or "").lower()
        update_intent = any(token in lowered_user for token in ("update", "modify", "edit", "fix", "patch", "refactor"))
        if update_intent:
            artifacts["files_updated"] = requested_files
        else:
            artifacts["files_created"] = requested_files

    issue_keys = sorted(set(_ISSUE_KEY_PATTERN.findall(full_text)))
    if issue_keys:
        artifacts["issue_keys"] = issue_keys

    feature_match = re.search(r"\bfeature(?:_name)?\s*[:=]\s*['\"]?([^\n,'\"]+)", full_text, re.IGNORECASE)
    if not feature_match:
        feature_match = re.search(r"^\s*Feature:\s*(.+)$", full_text, re.IGNORECASE | re.MULTILINE)
    if feature_match:
        artifacts["feature_name"] = feature_match.group(1).strip()

    scenario_match = re.search(r"^\s*Scenario:\s*(.+)$", full_text, re.IGNORECASE | re.MULTILINE)
    if scenario_match:
        artifacts["scenario_name"] = scenario_match.group(1).strip()

    for field in ("api_name", "module_name"):
        match = re.search(rf"\b{field}\s*[:=]\s*['\"]?([^\n,'\"]+)", full_text, re.IGNORECASE)
        if match:
            artifacts[field] = match.group(1).strip()

    confirmed_facts: List[str] = []
    normalized_user = user_message.strip()
    if was_waiting_user and normalized_user:
        confirmed_facts.append(normalized_user)
    if "expected" in normalized_user.lower() or "should" in normalized_user.lower():
        confirmed_facts.append(normalized_user)

    if confirmed_facts:
        artifacts["confirmed_facts"] = confirmed_facts

    return artifacts


def _merge_skill_artifacts(existing: Dict[str, Any], new_artifacts: Dict[str, Any]) -> Dict[str, Any]:
    """Merge artifacts conservatively without overriding existing key facts."""
    merged: Dict[str, Any] = dict(existing or {})

    for key, value in (new_artifacts or {}).items():
        if value in (None, "", [], {}):
            continue
        if isinstance(value, list):
            current = merged.get(key, [])
            if not isinstance(current, list):
                current = []
            seen = {str(item) for item in current}
            for item in value:
                marker = str(item)
                if marker not in seen:
                    current.append(item)
                    seen.add(marker)
            merged[key] = current
            continue
        if isinstance(value, dict):
            current = merged.get(key, {})
            if not isinstance(current, dict):
                current = {}
            current.update({k: v for k, v in value.items() if v is not None})
            merged[key] = current
            continue
        if key not in merged or not merged.get(key):
            merged[key] = value

    return merged


def _build_artifacts_summary(skill_session: SkillSession, max_items: int = 3, max_chars: int = 400) -> str:
    """Build a compact artifacts summary for prompt context."""
    artifacts = skill_session.artifacts or {}
    if not artifacts:
        return "(none)"

    lines: List[str] = []
    for key in ("requested_files", "files_created", "files_updated", "issue_keys", "confirmed_facts"):
        value = artifacts.get(key)
        if not value:
            continue
        if isinstance(value, list):
            preview = ", ".join(str(v) for v in value[:max_items])
            if len(value) > max_items:
                preview += ", ..."
            lines.append(f"- {key}: {preview}")
        else:
            lines.append(f"- {key}: {value}")

    for key in ("feature_name", "scenario_name", "api_name", "module_name"):
        if key in artifacts and artifacts[key]:
            lines.append(f"- {key}: {artifacts[key]}")

    summary = "\n".join(lines) if lines else "(none)"
    if len(summary) > max_chars:
        summary = summary[: max_chars - 3] + "..."
    return summary


async def compact_skill_session_async(skill_session: SkillSession, budget_tokens: int = 2000) -> SkillSession:
    """Compact skill session history to fit within token budget.
    
    Uses the existing compaction module to compress completed_steps.
    """
    try:
        from src.agents.compaction import compact_messages, AgentMessage, resolve_context_window_tokens
        
        # Convert completed_steps to AgentMessage format
        steps_as_messages = [
            AgentMessage(
                role="assistant",  # treat steps as assistant messages
                content=f"[{step.get('type', 'execute')}] {step.get('result', '')}",
                timestamp=step.get('timestamp', 0)
            )
            for step in skill_session.completed_steps
            if step.get('result')
        ]
        
        if not steps_as_messages:
            return skill_session
        
        # Estimate current tokens (rough estimate)
        current_tokens = sum(len(m.content) // 3 for m in steps_as_messages)
        
        if current_tokens <= budget_tokens:
            return skill_session  # No compaction needed
        
        # Use 64K context window for compaction
        context_window = resolve_context_window_tokens("default-64k")
        
        # Use existing compaction module
        compacted_messages, stats = await compact_messages(
            messages=steps_as_messages,
            budget_tokens=budget_tokens,
            context_window=context_window,
        )
        
        if stats and stats.dropped_messages > 0:
            # Convert back to completed_steps format
            new_steps = []
            for msg in compacted_messages:
                # Parse step type from content
                content = msg.content
                step_type = "execute"
                if content.startswith("[finish]"):
                    step_type = "finish"
                    content = content[8:].strip()
                elif content.startswith("[ask_user]"):
                    step_type = "ask_user"
                    content = content[10:].strip()
                new_steps.append({
                    "type": step_type,
                    "result": content,
                    "timestamp": msg.timestamp,
                    "_compacted": True,  # Mark as compacted
                })
            
            skill_session.completed_steps = new_steps
            logger.info(f"[SkillMode] Compacted {stats.dropped_messages} steps, kept {stats.kept_tokens} tokens")
        
        return skill_session
        
    except Exception as e:
        logger.warning(f"[SkillMode] Compaction failed: {e}, continuing without compaction")
        return skill_session


def compact_skill_session_sync(skill_session: SkillSession, max_steps: int = 20, max_chars: int = 4000) -> SkillSession:
    """Synchronous fallback compaction - simple truncation.
    
    Used when async compaction is not available or fails.
    """
    # Truncate completed_steps to max_steps
    if len(skill_session.completed_steps) > max_steps:
        # Keep first few (goal/plan) and last ones
        kept = skill_session.completed_steps[:3] + skill_session.completed_steps[-max_steps+3:]
        skill_session.completed_steps = kept
    
    # Truncate memory_summary
    if len(skill_session.memory_summary) > max_chars:
        skill_session.memory_summary = skill_session.memory_summary[-max_chars:]
    
    return skill_session
