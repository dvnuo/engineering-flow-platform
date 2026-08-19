"""Coarse, content-only accounting for a rendered provider request."""

from __future__ import annotations

from dataclasses import asdict
import json
import math
from typing import Any, Mapping

from ..llm.request import ProviderRequest, RequestMessage, RequestMessagePart


CATEGORY_LABELS = {
    "instructions": "Instructions",
    "tool_definitions": "Tool definitions",
    "conversation": "Conversation",
    "tool_activity": "Tool activity",
}


def estimate_tokens(value: Any, *, chars_per_token: float = 4.0) -> int:
    """Return a deliberately simple tokenizer-independent estimate."""

    if value is None or value == "" or value == [] or value == {}:
        return 0
    try:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        encoded = str(value or "")
    if not encoded:
        return 0
    divisor = chars_per_token if chars_per_token > 0 else 4.0
    return max(1, int(math.ceil(len(encoded) / divisor)))


def build_context_usage_snapshot(request: ProviderRequest) -> dict[str, Any]:
    """Describe total and four coarse categories for one model request.

    This intentionally counts only model-visible content and tool schemas. It
    never persists prompt text in the returned snapshot.
    """

    metadata = request.metadata if isinstance(request.metadata, Mapping) else {}
    budget = metadata.get("context_budget")
    budget = budget if isinstance(budget, Mapping) else {}
    chars_per_token = _positive_float(
        metadata.get("chars_per_token") or budget.get("chars_per_token"),
        default=4.0,
    )
    category_tokens = {category_id: 0 for category_id in CATEGORY_LABELS}

    for message in request.messages:
        for part in message.parts:
            category_id = _part_category(message, part)
            category_tokens[category_id] += estimate_tokens(
                _part_visible_value(part),
                chars_per_token=chars_per_token,
            )

    category_tokens["tool_definitions"] = estimate_tokens(
        [
            {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.json_schema,
            }
            for tool in request.tools
        ],
        chars_per_token=chars_per_token,
    )
    used_tokens = sum(category_tokens.values())
    context_window_tokens = _positive_int(
        metadata.get("context_window_tokens") or budget.get("context_window_tokens")
    )
    usage_percent = _percent(used_tokens, context_window_tokens)

    categories = []
    for category_id, label in CATEGORY_LABELS.items():
        tokens = category_tokens[category_id]
        categories.append(
            {
                "id": category_id,
                "label": label,
                "tokens": tokens,
                "percent_of_used": _percent(tokens, used_tokens),
                "percent_of_window": _percent(tokens, context_window_tokens),
            }
        )

    provider_id = _text(metadata.get("provider_id") or budget.get("provider_id"))
    model_id = _text(metadata.get("model_id") or budget.get("model_id"))
    return {
        "engine": "native",
        "scope": "last_request",
        "precision": "coarse",
        "measurement_method": "rendered_request_character_estimate",
        "used_tokens": used_tokens,
        "context_window_tokens": context_window_tokens,
        "usage_percent": usage_percent,
        "categories": categories,
        "model": {
            "provider_id": provider_id,
            "model_id": model_id,
            "context_window_tokens": context_window_tokens,
        },
    }


def _part_category(message: RequestMessage, part: RequestMessagePart) -> str:
    if part.type in {"tool_call", "tool_result", "attachment"}:
        return "tool_activity"
    if part.context is not None and part.context.type == "compaction_summary":
        return "conversation"
    if message.role == "system" or _instruction_metadata(message, part):
        return "instructions"
    return "conversation"


def _instruction_metadata(message: RequestMessage, part: RequestMessagePart) -> bool:
    instruction_kinds = {
        "agent_profile_context",
        "available_skills",
        "environment_context",
        "instruction_context",
        "skill_context",
        "system_prompt",
    }
    candidates: list[Any] = [message.metadata, part.metadata]
    if part.context is not None:
        candidates.append(part.context.metadata)
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            continue
        nested_values = [candidate]
        nested_values.extend(
            value for value in candidate.values() if isinstance(value, Mapping)
        )
        for values in nested_values:
            kind = _text(values.get("kind") or values.get("context_type"))
            if kind in instruction_kinds:
                return True
    return False


def _part_visible_value(part: RequestMessagePart) -> Any:
    if part.text is not None:
        return {"type": part.type, "text": part.text}
    if part.reasoning is not None:
        return {"type": part.type, "reasoning": part.reasoning.text}
    if part.tool_call is not None:
        return {
            "type": part.type,
            "tool_call": {
                "call_id": part.tool_call.call_id,
                "name": part.tool_call.tool_name,
                "arguments": part.tool_call.arguments,
                "arguments_text": part.tool_call.arguments_text,
            },
        }
    if part.tool_result is not None:
        return {
            "type": part.type,
            "tool_result": {
                "call_id": part.tool_result.call_id,
                "name": part.tool_result.tool_name,
                "content": part.tool_result.content,
                "output": part.tool_result.output,
                "error": part.tool_result.error,
                "attachments": [asdict(item) for item in part.tool_result.attachments],
            },
        }
    if part.context is not None:
        return {
            "type": part.type,
            "context": {
                "type": part.context.type,
                "text": part.context.text,
            },
        }
    if part.attachment is not None:
        return {
            "type": part.type,
            "attachment": {
                "mime_type": part.attachment.mime_type,
                "filename": part.attachment.filename,
                "url": part.attachment.url,
                "text_ref": part.attachment.text_ref,
            },
        }
    return {"type": part.type}


def _percent(numerator: int, denominator: int | None) -> float | None:
    if not denominator or denominator <= 0:
        return None
    return round((numerator / denominator) * 100.0, 1)


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _positive_float(value: Any, *, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


__all__ = ["CATEGORY_LABELS", "build_context_usage_snapshot", "estimate_tokens"]
