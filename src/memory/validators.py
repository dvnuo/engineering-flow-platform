"""Validators for MemoryOps schema."""

from typing import Any, Dict, List, Tuple


# Valid operations
VALID_OPS = {"ADD", "UPDATE", "MERGE", "DELETE", "NOOP"}

# Valid memory types
VALID_TYPES = {"summary", "decision", "fact", "preference"}

# Max lengths
MAX_OPS_COUNT = 5
MAX_CONTENT_LENGTH = 600
MAX_TAGS = 5

# Noise patterns to filter out
NOISE_PATTERNS = [
    "assistant saved",
    "assistant remembers", 
    "assistant will call",
    "saved in the user's profile",
    "i will call you",
    "i've saved",
    "i've noted",
    "i will remember",
    "assistant decided",
    "as an AI",
]


def validate_memory_ops(ops: List[Dict[str, Any]]) -> Tuple[bool, str]:
    """Validate MemoryOps list.
    
    Args:
        ops: List of memory operations
        
    Returns:
        Tuple of (is_valid, error_message)
    """
    if not isinstance(ops, list):
        return False, "ops must be a list"

    if len(ops) > MAX_OPS_COUNT:
        return False, f"Too many ops (max {MAX_OPS_COUNT})"

    for i, op in enumerate(ops):
        if not isinstance(op, dict):
            return False, f"Op {i} must be a dict"

        # Validate 'op' field
        op_type = op.get("op")
        if op_type not in VALID_OPS:
            return False, f"Op {i}: invalid op '{op_type}' (must be one of {VALID_OPS})"

        # Skip further validation for NOOP
        if op_type == "NOOP":
            continue

        # Validate 'type' field
        mem_type = op.get("type")
        if mem_type not in VALID_TYPES:
            return False, f"Op {i}: invalid type '{mem_type}' (must be one of {VALID_TYPES})"

        # Validate 'content' field
        content = op.get("content", "")
        if not isinstance(content, str):
            return False, f"Op {i}: content must be a string"
        if len(content) > MAX_CONTENT_LENGTH:
            return False, f"Op {i}: content too long (max {MAX_CONTENT_LENGTH} chars)"

        # Validate 'confidence' field
        confidence = op.get("confidence", 0.5)
        if not isinstance(confidence, (int, float)):
            return False, f"Op {i}: confidence must be a number"
        if not (0 <= confidence <= 1):
            return False, f"Op {i}: confidence must be between 0 and 1"

        # Validate 'tags' field
        tags = op.get("tags", [])
        if not isinstance(tags, list):
            return False, f"Op {i}: tags must be a list"
        if len(tags) > MAX_TAGS:
            return False, f"Op {i}: too many tags (max {MAX_TAGS})"
        for tag in tags:
            if not isinstance(tag, str):
                return False, f"Op {i}: tag must be string"

        # Validate 'source' field
        source = op.get("source")
        if source is not None:
            if not isinstance(source, dict):
                return False, f"Op {i}: source must be a dict"
            if "session_id" not in source or "turn_id" not in source:
                return False, f"Op {i}: source must have session_id and turn_id"

    return True, ""


def filter_noise_ops(ops: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Filter out noisy operations."""
    filtered: List[Dict[str, Any]] = []
    # Be defensive: ops may contain non-dicts or malformed fields from LLM output
    for op in ops:
        if not isinstance(op, dict):
            # Skip non-dict entries to avoid attribute errors
            continue

        # Safely normalize content to lowercase string
        raw_content = op.get("content", "")
        if not isinstance(raw_content, str):
            raw_content = str(raw_content) if raw_content is not None else ""
        content = raw_content.lower()

        # Skip if content matches noise patterns
        if any(p in content for p in NOISE_PATTERNS):
            continue

        # Safely normalize tags to a list of lowercase strings
        raw_tags = op.get("tags", [])
        if not isinstance(raw_tags, list):
            raw_tags = [raw_tags]
        tags = [t.lower() for t in raw_tags if isinstance(t, str)]

        # Skip if tags contain personal_information with fact/preference
        op_type = op.get("type")
        if (
            "personal_information" in tags
            and isinstance(op_type, str)
            and op_type in ("fact", "preference")
        ):
            continue
        filtered.append(op)
    return filtered


def sanitize_memory_ops(ops: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Sanitize and normalize MemoryOps.
    
    Args:
        ops: List of memory operations
        
    Returns:
        Sanitized list of ops
    """
    # First filter noise
    ops = filter_noise_ops(ops)
    
    sanitized = []
    for op in ops:
        if not isinstance(op, dict):
            continue

        # Only process valid ops
        if op.get("op") not in VALID_OPS:
            continue

        # Normalize op
        raw_confidence = op.get("confidence", 0.5)
        try:
            numeric_confidence = float(raw_confidence)
        except (TypeError, ValueError):
            numeric_confidence = 0.5
        bounded_confidence = min(1.0, max(0.0, numeric_confidence))
        normalized = {
            "op": op.get("op", "NOOP"),
            "type": op.get("type", "summary"),
            "content": op.get("content", "")[:MAX_CONTENT_LENGTH],
            "confidence": bounded_confidence,
            "tags": list(op.get("tags", []))[:MAX_TAGS],
        }
        
        # Ensure source exists (use placeholder if not set)
        if "source" in op:
            normalized["source"] = op["source"]

        # Remove None values
        normalized = {k: v for k, v in normalized.items() if v is not None}
        sanitized.append(normalized)

    return sanitized[:MAX_OPS_COUNT]
