"""String truncation utilities."""
import json
from typing import Any

def truncate(text: str, max_length: int = 500, suffix: str = "...") -> str:
    """Safely truncate string, never raises error.
    
    Args:
        text: String to truncate
        max_length: Maximum length before truncation
        suffix: Suffix to append when truncated
        
    Returns:
        Truncated string with suffix if needed
    """
    if not text:
        return ""
    text = str(text)
    if len(text) <= max_length:
        return text
    return text[:max_length] + suffix


def truncate_with_count(text: str, max_length: int = 500) -> str:
    """Truncate and show remaining character count.
    
    Args:
        text: String to truncate
        max_length: Maximum length before truncation
        
    Returns:
        Truncated string with count info if needed
    """
    if not text:
        return "(empty)"
    text = str(text)
    if len(text) <= max_length:
        return text
    return f"{text[:max_length]}... [{len(text) - max_length} chars hidden]"


def truncate_json(data: Any, max_length: int = 500) -> str:
    """Truncate JSON/string for logging.
    
    Args:
        data: Data to truncate (dict, list, or string)
        max_length: Maximum length before truncation
        
    Returns:
        Truncated JSON string with count info if needed
    """
    text = json.dumps(data, indent=2, default=str)
    return truncate_with_count(text, max_length)


__all__ = ["truncate", "truncate_with_count", "truncate_json"]
