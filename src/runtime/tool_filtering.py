"""Helpers for global llm.tools filtering semantics."""

from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatchcase
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set


@dataclass(frozen=True)
class LlmToolsSpec:
    configured: bool
    mode: str  # all | none | patterns
    patterns: List[str]
    raw_value: Any = None


@dataclass(frozen=True)
class FilteredToolSchemasResult:
    filtered_schemas: List[Dict[str, Any]]
    allowed_tool_names: List[str]
    matched_tool_names: List[str]
    unmatched_patterns: List[str]
    configured: bool
    mode: str
    patterns: List[str]


def extract_tool_name(tool_schema: Dict[str, Any]) -> Optional[str]:
    """Extract tool name from an OpenAI function-style or flat schema."""
    if not isinstance(tool_schema, dict):
        return None
    function_obj = tool_schema.get("function")
    if isinstance(function_obj, dict):
        function_name = function_obj.get("name")
        if isinstance(function_name, str) and function_name.strip():
            return function_name.strip()
    tool_name = tool_schema.get("name")
    if isinstance(tool_name, str) and tool_name.strip():
        return tool_name.strip()
    return None


def _dedupe_preserve_order(values: Sequence[str]) -> List[str]:
    seen: Set[str] = set()
    deduped: List[str] = []
    for value in values:
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(value)
    return deduped


def normalize_llm_tools_spec(llm_config: Dict[str, Any]) -> LlmToolsSpec:
    """Normalize llm.tools configuration while preserving missing vs explicit-empty."""
    if not isinstance(llm_config, dict):
        return LlmToolsSpec(configured=False, mode="all", patterns=[], raw_value=None)

    if "tools" not in llm_config:
        return LlmToolsSpec(configured=False, mode="all", patterns=[], raw_value=None)

    raw_value = llm_config.get("tools")

    if raw_value is None:
        return LlmToolsSpec(configured=True, mode="none", patterns=[], raw_value=raw_value)

    if isinstance(raw_value, str):
        value = raw_value.strip()
        if not value:
            return LlmToolsSpec(configured=True, mode="none", patterns=[], raw_value=raw_value)
        if value == "*":
            return LlmToolsSpec(configured=True, mode="all", patterns=[], raw_value=raw_value)
        return LlmToolsSpec(configured=True, mode="patterns", patterns=[value], raw_value=raw_value)

    if isinstance(raw_value, list):
        normalized: List[str] = []
        for idx, item in enumerate(raw_value):
            if not isinstance(item, str):
                raise ValueError(f"llm.tools[{idx}] must be a string, got {type(item).__name__}")
            item_text = item.strip()
            if item_text:
                normalized.append(item_text)

        normalized = _dedupe_preserve_order(normalized)

        if any(item == "*" for item in normalized):
            return LlmToolsSpec(configured=True, mode="all", patterns=[], raw_value=raw_value)
        if not normalized:
            return LlmToolsSpec(configured=True, mode="none", patterns=[], raw_value=raw_value)
        return LlmToolsSpec(configured=True, mode="patterns", patterns=normalized, raw_value=raw_value)

    raise ValueError(f"llm.tools must be a string, list, or null, got {type(raw_value).__name__}")


def filter_tool_schemas_for_llm(
    tool_schemas: List[Dict[str, Any]],
    llm_config: Dict[str, Any],
) -> FilteredToolSchemasResult:
    """Filter tool schemas by llm.tools policy without mutating schemas."""
    spec = normalize_llm_tools_spec(llm_config)

    if spec.mode == "all":
        names = [name for name in (extract_tool_name(schema) for schema in tool_schemas) if name]
        return FilteredToolSchemasResult(
            filtered_schemas=list(tool_schemas),
            allowed_tool_names=names,
            matched_tool_names=names,
            unmatched_patterns=[],
            configured=spec.configured,
            mode=spec.mode,
            patterns=list(spec.patterns),
        )

    if spec.mode == "none":
        return FilteredToolSchemasResult(
            filtered_schemas=[],
            allowed_tool_names=[],
            matched_tool_names=[],
            unmatched_patterns=[],
            configured=spec.configured,
            mode=spec.mode,
            patterns=list(spec.patterns),
        )

    matched_schemas: List[Dict[str, Any]] = []
    matched_names: List[str] = []
    matched_patterns: Set[str] = set()
    patterns_lower = [(pattern, pattern.lower()) for pattern in spec.patterns]

    for schema in tool_schemas:
        name = extract_tool_name(schema)
        if not name:
            continue
        name_lower = name.lower()
        schema_matches = False
        for pattern, pattern_lower in patterns_lower:
            if fnmatchcase(name_lower, pattern_lower):
                schema_matches = True
                matched_patterns.add(pattern.lower())
        if schema_matches:
            matched_schemas.append(schema)
            matched_names.append(name)

    unmatched_patterns = [pattern for pattern in spec.patterns if pattern.lower() not in matched_patterns]

    return FilteredToolSchemasResult(
        filtered_schemas=matched_schemas,
        allowed_tool_names=matched_names,
        matched_tool_names=matched_names,
        unmatched_patterns=unmatched_patterns,
        configured=spec.configured,
        mode=spec.mode,
        patterns=list(spec.patterns),
    )


def is_tool_name_enabled_for_llm(tool_name: str, llm_config: Dict[str, Any]) -> bool:
    """Return whether tool_name is enabled by llm.tools policy."""
    if not isinstance(tool_name, str) or not tool_name.strip():
        return False
    spec = normalize_llm_tools_spec(llm_config)
    if spec.mode == "all":
        return True
    if spec.mode == "none":
        return False
    lowered_name = tool_name.strip().lower()
    return any(fnmatchcase(lowered_name, pattern.lower()) for pattern in spec.patterns)


def intersect_tool_schemas_by_names(
    tool_schemas: List[Dict[str, Any]],
    allowed_names: Iterable[str],
) -> List[Dict[str, Any]]:
    """Intersect tool schemas by exact tool name match."""
    allowed_name_set = {str(name) for name in allowed_names if isinstance(name, str) and name}
    return [
        tool_schema
        for tool_schema in tool_schemas
        if (extract_tool_name(tool_schema) or "") in allowed_name_set
    ]
