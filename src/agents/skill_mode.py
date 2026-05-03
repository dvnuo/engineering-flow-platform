"""Legacy skill-mode session helpers.

NOTE FOR MAINTAINERS:
- This module is a legacy compatibility surface, not the default matched-skill live path.
- Normal matched skill / active skill runtime now flows through Agent.process + active-skill
  contract continuation + runtime prompt assembly (src/skills/runtime.py).
- Keep this file for explicit legacy paths and compatibility tests; do not treat it as the
  primary place to tune modern response-flow behavior.
- Stepwise/marker prompts here should not be read as "global default behavior". Direct-path
  runtime remains "complete directly when information is sufficient" unless policy/metadata/
  complexity explicitly requires staged or plan-first handling.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from src.agents.llm import _normalize_provider_key, llm_client
from src.runtime.output_controller import call_llm_with_output_control
from src.config import config
from src.runtime.response_flow_policy import decide_response_flow
from src.skills.registry import Skill

logger = logging.getLogger(__name__)


def list_skill_reference_files(skill_path: str) -> str:
    """Build a formatted reference summary string for a skill directory.
    
    Supports two patterns:
    1. references/ folder: skill_path/references/*.md (preferred)
    2. Root-level refs: skill_path/ref-*.md or skill_path/*.md (excluding skill.md)
    
    Args:
        skill_path: Path to the skill directory
        
    Returns:
        Human-readable summary text listing available reference files for tool use.
        (Legacy behavior: this function returns a string, not a List[str].)
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
        # Pattern 2: Root-level reference files (ref-*.md or any *.md except skill.md)
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
    # Runtime control fields (backward-compatible via from_dict defaults)
    tool_round_count: int = 0
    llm_call_count: int = 0
    finalizer_attempts: int = 0
    no_progress_count: int = 0
    last_progress_signature: str = ""
    termination_reason: str = ""
    transition: str = ""
    finalizer_state: str = "idle"  # idle/running/succeeded/retryable_failed/terminal_failed
    execution_mode: str = ""  # "", "readonly_lookup", "producing_output", "waiting_user"
    last_tool_name: str = ""
    last_tool_args_signature: str = ""
    last_tool_output_signature: str = ""

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
            tool_round_count=int(data.get("tool_round_count", 0) or 0),
            llm_call_count=int(data.get("llm_call_count", 0) or 0),
            finalizer_attempts=int(data.get("finalizer_attempts", 0) or 0),
            no_progress_count=int(data.get("no_progress_count", 0) or 0),
            last_progress_signature=data.get("last_progress_signature", "") or "",
            termination_reason=data.get("termination_reason", "") or "",
            transition=data.get("transition", "") or "",
            finalizer_state=data.get("finalizer_state", "idle") or "idle",
            execution_mode=data.get("execution_mode", "") or "",
            last_tool_name=data.get("last_tool_name", "") or "",
            last_tool_args_signature=data.get("last_tool_args_signature", "") or "",
            last_tool_output_signature=data.get("last_tool_output_signature", "") or "",
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
    strategy = getattr(skill, "strategy", None) or []
    strategy_hint = "\n".join(f"- {s}" for s in strategy) if strategy else "(none)"
    return (
        f"User request: {user_message}\n"
        f"Skill strategy hints:\n{strategy_hint}\n"
        "Generate the initial lightweight plan now."
    )


def _build_skill_mode_system_prompt(
    skill: Skill,
    skill_session: SkillSession,
    *,
    execution_style: str = "direct",
    ask_user_policy: str = "blocked_only",
) -> str:
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
    strategy = getattr(skill, "strategy", None) or []
    strategy_hint = "\n".join(f"- {s}" for s in strategy) if strategy else "(none)"
    artifacts_summary = _build_artifacts_summary(skill_session)
    
    # Load skill references
    references = list_skill_reference_files(getattr(skill, 'path', '') or '')
    skill_scripts = _list_skill_scripts(getattr(skill, 'path', '') or '')

    output_rules = [
        "1) First line MUST be exactly one marker: [EXECUTE] or [ASK_USER] or [FINISH]",
        "2) No other prefix before first line",
        "3) Plain text: no markdown wrappers needed",
        "4) Code blocks: ALWAYS use triple backticks with language hint (```python, ```javascript, etc.)",
    ]
    if execution_style == "stepwise":
        output_rules.extend(
            [
                "5) Advance only ONE small step this turn",
                "6) If key missing info blocks progress -> [ASK_USER] and ask ONE minimal necessary question with brief reason",
            ]
        )
    else:
        output_rules.extend(
            [
                "5) Complete the skill directly when enough information is available",
                "6) Use [ASK_USER] only when required missing information blocks safe progress",
                "7) Do not ask user confirmation merely to continue ordinary work",
            ]
        )
    output_rules.extend(
        [
            "8) If enough info and task can progress -> [EXECUTE]",
            "9) If goal is done -> [FINISH] with concise final summary",
            "10) Tools should only be used when they clearly help progress the current skill",
            "11) Do not call tools speculatively",
            "12) If key user information is missing, ask user instead of over-searching with tools",
            "13) If you create or update a file, always show the complete code in a markdown code block",
            f"14) ASK_USER policy: {ask_user_policy}",
        ]
    )

    skill_name = getattr(skill, "name", "") or "skill"
    skill_description = getattr(skill, "description", None) or skill_name
    skill_goal = skill_session.goal or skill_description

    return (
        "You are running an active skill-mode session.\n"
        f"Skill: {skill_name}\n"
        f"Goal: {skill_goal}\n"
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
        + "\n".join(output_rules)
        + "\n"
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
        normalized_steps = [{"id": "complete_task", "type": "execute", "title": "Complete the request"}]
    return goal, normalized_steps


def resolve_skill_response_flow(skill: Skill, user_message: str, *, request_estimated_tokens: Optional[int] = None, prompt_budget_tokens: Optional[int] = None) -> Dict[str, Any]:
    llm_cfg = config.llm if isinstance(config.llm, dict) else {}
    decision = decide_response_flow(
        config_block=llm_cfg.get("response_flow"),
        user_text=user_message,
        request_estimated_tokens=request_estimated_tokens,
        prompt_budget_tokens=prompt_budget_tokens,
        planning_mode=str(getattr(skill, "planning_mode", "auto") or "auto"),
        staging_mode=str(getattr(skill, "staging_mode", "auto") or "auto"),
        execution_style=str(getattr(skill, "execution_style", "") or ""),
        ask_user_policy=str(getattr(skill, "ask_user_policy", "") or ""),
    )
    return {
        "plan_required": decision.plan_required,
        "staging_required": decision.staging_required,
        "execution_style": decision.execution_style,
        "ask_user_policy": decision.ask_user_policy,
        "reasons": list(decision.reasons),
    }


async def generate_initial_skill_plan(
    skill: Skill,
    user_message: str,
    model: Optional[str] = None,
    return_usage: bool = False,
) -> Tuple[str, List[Dict[str, str]], Dict[str, int]]:
    """Generate initial skill plan.
    
    Args:
        skill: The skill to generate plan for
        user_message: User's request message
        model: Optional model override
        return_usage: Backward-compatible argument (result shape is always 3-tuple)
    
    Returns:
        Always returns (goal, steps, usage_dict).
    """
    # Narrow top-level skill-mode planning entrypoint now routes through ExecutionBus.
    # TODO(phase1): evaluate routing deeper internal skill helper calls through ExecutionBus after compatibility validation.
    from src.runtime.chat_orchestration_adapter import execute_skill_orchestration

    async def _skill_plan_handler(_request):
        return await _generate_initial_skill_plan_direct(skill=skill, user_message=user_message, model=model)

    execution_result = await execute_skill_orchestration(
        source_ref="skill_mode.generate_initial_skill_plan",
        session_id=None,
        input_payload={
            "skill_name": skill.name,
            "user_message": user_message,
            "model": model,
        },
        metadata={"entrypoint": "skill_mode.generate_initial_skill_plan"},
        custom_skill_handler=_skill_plan_handler,
    )
    default_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    if execution_result.status == "error":
        logger.warning("[SkillMode] Bus planner failed, using direct planner fallback")
        direct_result = await _generate_initial_skill_plan_direct(skill=skill, user_message=user_message, model=model)
        goal = str(direct_result.get("goal") or skill.description)
        steps = direct_result.get("steps") if isinstance(direct_result.get("steps"), list) else []
        usage_data = direct_result.get("usage") if isinstance(direct_result.get("usage"), dict) else default_usage
    else:
        payload = execution_result.output_payload if isinstance(execution_result.output_payload, dict) else {}
        normalized_goal, normalized_steps = _normalize_plan(payload, fallback_goal=skill.description)
        payload_steps = payload.get("steps")
        goal = str(payload.get("goal") or normalized_goal)
        steps = payload_steps if isinstance(payload_steps, list) and payload_steps else normalized_steps
        usage_data = payload.get("usage") if isinstance(payload.get("usage"), dict) else default_usage

    # Compatibility: keep stable 3-tuple arity for existing callers in core/runtime paths.
    _ = return_usage
    return goal, steps, usage_data


async def _generate_initial_skill_plan_direct(skill: Skill, user_message: str, model: Optional[str] = None) -> Dict[str, Any]:
    system_prompt = _build_skill_plan_system_prompt(skill)
    user_prompt = _build_skill_plan_user_prompt(skill, user_message)

    provider = (config.llm.get("provider") or getattr(llm_client, "default_provider", "openai")).lower()
    kwargs = {
        "input_items": [{"role": "user", "content": [{"type": "input_text", "text": user_prompt}]}],
        "system_prompt": system_prompt,
        "tools": [],
        "reasoning_replay": False,
        "provider": _normalize_provider_key(provider),
    }
    if model:
        kwargs["model"] = model

    result, _diag = await call_llm_with_output_control(
        llm_client=llm_client,
        llm_kwargs=kwargs,
        session_id="unknown_session",
        stage="skill_initial_plan",
        context_state={"budget": {}},
        active_skill=skill,
        latest_user_text=user_message,
        max_chat_output_chars=None,
    )
    content = (result.get("content") or "").strip()

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
    return {"goal": goal, "steps": steps, "usage": usage_data}


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
            max_tokens=budget_tokens,
            context_window=context_window,
            recent_count=3,
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


# Backward compatibility alias
_load_skill_references = list_skill_reference_files
