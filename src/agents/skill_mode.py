"""Lightweight skill-mode session helpers."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union

from src.agents.llm import _normalize_provider_key, llm_client
from src.config import config
from src.skills.registry import Skill

logger = logging.getLogger(__name__)


# Type alias for consistent return shape (goal, steps, usage)
SkillPlanResult = Tuple[str, List[Dict[str, str]], Dict[str, int]]


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
    completed = "\n".join(
        f"- {item.get('result', '')}" for item in skill_session.completed_steps[-3:] if item.get("result")
    ) or "(none)"
    plan = "\n".join(
        f"- [{step.get('type', 'execute')}] {step.get('title', '')}" for step in skill_session.plan
    ) or "(none)"

    # Include skill strategy for ongoing sessions
    strategy_hint = "\n".join(f"- {s}" for s in skill.strategy) if skill.strategy else "(none)"
    artifacts_summary = _build_artifacts_summary(skill_session)

    return (
        "You are running an active skill-mode session.\n"
        f"Skill: {skill.name}\n"
        f"Goal: {skill_session.goal or skill.description}\n"
        f"Plan:\n{plan}\n\n"
        f"Completed summary:\n{completed}\n\n"
        f"Memory summary:\n{skill_session.memory_summary or '(empty)'}\n\n"
        f"Known artifacts:\n{artifacts_summary}\n\n"
        f"Strategy hints:\n{strategy_hint}\n\n"
        "Output rules (STRICT):\n"
        "1) First line MUST be exactly one marker: [EXECUTE] or [ASK_USER] or [FINISH]\n"
        "2) No other prefix before first line\n"
        "3) No markdown wrappers\n"
        "4) Advance only ONE small step this turn\n"
        "5) If key missing info blocks progress -> [ASK_USER] and ask ONE minimal necessary question with brief reason\n"
        "6) If enough info and task can progress -> [EXECUTE]\n"
        "7) If goal is done -> [FINISH] with concise final summary\n"
        "8) Tools should only be used when they clearly help progress the current skill\n"
        "9) Do not call tools speculatively\n"
        "10) If key user information is missing, ask user instead of over-searching with tools\n"
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


def _update_skill_memory_summary(skill_session: SkillSession, user_message: str, latest_result: str, max_chars: int = 1200) -> str:
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
    r"(?<![\w/.-])([A-Za-z0-9_./-]+\.(?:feature|java|xml|json|ya?ml|md))(?![\w.-])",
    re.IGNORECASE,
)
_ISSUE_KEY_PATTERN = re.compile(r"\b[A-Z][A-Z0-9]+-\d+\b")


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
    for key in ("files_created", "files_updated", "issue_keys", "confirmed_facts"):
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
