import json
import re
from typing import Any, Optional

ISSUE_KEY_RE = re.compile(r"\b[A-Z][A-Z0-9_]*-\d+\b", re.IGNORECASE)


def validate_issue_key(key: str) -> str:
    normalized = str(key or "").strip().upper()
    if not normalized or not ISSUE_KEY_RE.fullmatch(normalized):
        raise ValueError(f"Invalid issue key: {key}")
    return normalized


def dedupe_issue_keys(keys: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for key in keys or []:
        normalized = validate_issue_key(key)
        if normalized in seen:
            continue
        seen.add(normalized)
        out.append(normalized)
    return out


def extract_issue_keys_from_text(text: str) -> list[str]:
    raw = str(text or "")
    matches = []
    for m in ISSUE_KEY_RE.finditer(raw):
        start, end = m.span()
        prev_c = raw[start - 1] if start > 0 else ""
        next_c = raw[end] if end < len(raw) else ""
        if prev_c == "/" or next_c == "/":
            continue
        matches.append(m.group(0))
    return dedupe_issue_keys(matches)


def extract_output_directory_from_text(text: str) -> Optional[str]:
    raw = str(text or "")
    patterns = [
        r"(?:save\s*to\s*folder|save到folder|folder|目录)\s*[：:]\s*([^\s，,。]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, raw, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return None


def _normalize_limits(page_size: int, max_issues: int) -> tuple[int, int]:
    try:
        page_size_v = int(page_size)
    except Exception:
        page_size_v = 50
    try:
        max_issues_v = int(max_issues)
    except Exception:
        max_issues_v = 100
    page_size_v = max(1, min(100, page_size_v))
    max_issues_v = max(1, max_issues_v)
    return page_size_v, max_issues_v


def normalize_jira_issue_selector(
    input: Any = None,
    issue_keys: Optional[list[str]] = None,
    jql: Optional[str] = None,
    page_size: int = 50,
    max_issues: int = 100,
) -> dict:
    page_size_v, max_issues_v = _normalize_limits(page_size, max_issues)
    selector = {
        "selector_type": "issue_keys",
        "issue_keys": [],
        "jql": "",
        "page_size": page_size_v,
        "max_issues": max_issues_v,
        "truncated": False,
        "partial_reasons": [],
    }

    if issue_keys:
        keys = dedupe_issue_keys(issue_keys)
        if len(keys) > max_issues_v:
            selector["truncated"] = True
            selector["partial_reasons"].append(f"max_issues_truncated:{max_issues_v}")
            keys = keys[:max_issues_v]
        selector["selector_type"] = "issue_keys"
        selector["issue_keys"] = keys
        return selector

    if jql and input in (None, "", []):
        selector["selector_type"] = "jql"
        selector["jql"] = str(jql).strip()
        return selector

    if isinstance(input, dict):
        merged_page_size = input.get("page_size", page_size_v)
        merged_max_issues = input.get("max_issues", max_issues_v)
        if input.get("jql"):
            normalized = normalize_jira_issue_selector(
                input=None,
                jql=input.get("jql"),
                page_size=merged_page_size,
                max_issues=merged_max_issues,
            )
            return normalized
        if input.get("issue_keys"):
            normalized = normalize_jira_issue_selector(
                input=None,
                issue_keys=input.get("issue_keys"),
                page_size=merged_page_size,
                max_issues=merged_max_issues,
            )
            return normalized

    if isinstance(input, list):
        return normalize_jira_issue_selector(
            input=None,
            issue_keys=[str(x) for x in input],
            page_size=page_size_v,
            max_issues=max_issues_v,
        )

    if isinstance(input, str):
        s = input.strip()
        if s.lower().startswith("jql:"):
            selector["selector_type"] = "jql"
            selector["jql"] = s[4:].strip()
            return selector

        if (s.startswith("[") and s.endswith("]")) or (s.startswith("{") and s.endswith("}")):
            try:
                decoded = json.loads(s)
                return normalize_jira_issue_selector(
                    input=decoded,
                    page_size=page_size_v,
                    max_issues=max_issues_v,
                )
            except Exception:
                pass

        keys = extract_issue_keys_from_text(s)
        if not keys:
            for token in re.split(r"[\s,\n\r\t]+", s):
                token = token.strip()
                if not token:
                    continue
                try:
                    keys.append(validate_issue_key(token))
                except Exception:
                    continue
            keys = dedupe_issue_keys(keys)

        if keys:
            if len(keys) > max_issues_v:
                selector["truncated"] = True
                selector["partial_reasons"].append(f"max_issues_truncated:{max_issues_v}")
                keys = keys[:max_issues_v]
            selector["selector_type"] = "issue_keys"
            selector["issue_keys"] = keys
            return selector

    raise ValueError("Unable to resolve Jira issue selector from input/issue_keys/jql")
