"""Active skill session contract helpers."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any, Optional

from src.skills.runtime import SkillRuntimeConfig

ACTIVE_SKILL_CONTRACT_VERSION = "active_skill_contract.v1"

_FINISHED_STATUSES = {"finished", "completed", "cancelled", "canceled", "done", "cleared"}
_CLEAR_COMMANDS = {
    "/skill clear",
    "/skill exit",
    "/skill stop",
    "/skill reset",
    "/skill off",
    "/skill done",
}
_SKILL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _shorten(value: Any, max_len: int) -> str:
    text = str(value or "").strip()
    return text[:max_len]


def is_clear_active_skill_command(message: str) -> bool:
    normalized = str(message or "").strip().lower()
    return normalized in _CLEAR_COMMANDS


def parse_explicit_skill_switch_name(message: str) -> str:
    raw = str(message or "").strip()
    if not raw:
        return ""
    if is_clear_active_skill_command(raw):
        return ""

    lowered = raw.lower()
    if lowered.startswith("/skill "):
        remainder = raw[len("/skill ") :].strip()
        if not remainder:
            return ""
        parts = remainder.split()
        if not parts:
            return ""
        command = parts[0].lower()
        candidate = parts[1] if command in {"switch", "use", "activate"} and len(parts) > 1 else parts[0]
        candidate = candidate.strip()
        return candidate if _SKILL_NAME_PATTERN.match(candidate) else ""

    if raw.startswith("/") and not lowered.startswith("/skill"):
        slash_token = raw.split()[0].strip()
        candidate = slash_token[1:]
        return candidate if _SKILL_NAME_PATTERN.match(candidate) else ""

    return ""


def get_contract_skill_name(contract: Optional[dict]) -> str:
    if not isinstance(contract, dict):
        return ""
    return str(contract.get("skill_name") or contract.get("skill") or "").strip()


def is_active_skill_contract_usable(contract: Optional[dict]) -> bool:
    if not isinstance(contract, dict):
        return False
    skill_name = get_contract_skill_name(contract)
    if not skill_name:
        return False
    status = str(contract.get("status") or "").strip().lower()
    if status in _FINISHED_STATUSES:
        return False
    return True


def _build_skill_hash(*, skill: Any) -> str:
    content = "\n".join(
        [
            str(getattr(skill, "name", "") or ""),
            str(getattr(skill, "version", "") or ""),
            str(getattr(skill, "description", "") or ""),
            str(getattr(skill, "body", "") or ""),
        ]
    )
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:12]


def build_active_skill_contract(
    *,
    skill,
    runtime_config: SkillRuntimeConfig,
    user_message: str,
    existing_contract: Optional[dict] = None,
    activation_reason: str = "matched",
) -> dict:
    now = _utc_now_iso()
    existing = existing_contract if isinstance(existing_contract, dict) else {}

    current_skill_name = str(runtime_config.skill_name or "").strip()
    existing_skill_name = get_contract_skill_name(existing)
    same_skill = bool(existing_skill_name and existing_skill_name == current_skill_name)

    prior_turn_count = existing.get("turn_count") if same_skill else None
    try:
        prior_turn_count_int = int(prior_turn_count)
    except (TypeError, ValueError):
        prior_turn_count_int = 0

    original_request = (
        _shorten(existing.get("original_user_request", ""), 2000)
        if same_skill and existing.get("original_user_request")
        else _shorten(user_message, 2000)
    )
    goal = (
        _shorten(existing.get("goal", ""), 500)
        if same_skill and existing.get("goal")
        else _shorten(user_message, 500)
    )
    created_at = str(existing.get("created_at") or "").strip() if same_skill else ""

    return {
        "schema_version": ACTIVE_SKILL_CONTRACT_VERSION,
        "skill_name": current_skill_name,
        "skill_version": str(getattr(skill, "version", "") or ""),
        "skill_hash": _build_skill_hash(skill=skill),
        "status": "active",
        "activation_reason": str(activation_reason or "matched"),
        "original_user_request": original_request,
        "goal": goal,
        "last_user_message": _shorten(user_message, 1000),
        "turn_count": prior_turn_count_int + 1 if same_skill else 1,
        "allowed_tools": list(runtime_config.allowed_tools or []),
        "tool_policy_declared": bool(runtime_config.tool_policy_declared),
        "task_tools": list(runtime_config.task_tools or []),
        "model_override": runtime_config.model_override,
        "hooks": list(runtime_config.hooks or []),
        "workdir": runtime_config.workdir,
        "references": list(runtime_config.references or []),
        "prompt_contract_summary": _shorten(runtime_config.prompt_blocks.developer_instructions, 2000),
        "created_at": created_at or now,
        "updated_at": now,
    }
