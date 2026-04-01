"""Policies for deciding whether a tool result can be returned directly."""

import re
from typing import Any

_JIRA_DETAIL_TOOLS = {"jira_get_issue", "jira_get_issue_by_url"}
_JIRA_TRANSFORM_INTENT_KEYWORDS = {
    "summarize", "summary", "analyze", "analysis", "explanation", "explain",
    "compare", "rewrite", "extract", "generate", "create", "update",
    "comment", "transition", "refine",
}
_JIRA_RETRIEVAL_HINTS = {
    "get issue", "show issue", "read issue", "open issue",
    "issue detail", "jira detail", "fetch issue", "retrieve issue",
}
_JIRA_MUTATION_INTENT_KEYWORDS = {
    "assign", "assignee", "update", "edit", "modify", "change", "set",
    "add", "comment", "transition", "move", "status", "resolve", "close",
    "reopen", "link", "attach", "remove", "delete", "create", "generate",
}
_JIRA_SEQUENCE_CONNECTORS = {"and", "then", "after", "also"}
_JIRA_SEQUENCE_WORDS = {"then", "after", "next"}


def _message_has_any_keyword(text: str, keywords: set[str]) -> bool:
    normalized_text = text.lower()
    for keyword in keywords:
        keyword_tokens = keyword.lower().split()
        if len(keyword_tokens) > 1:
            phrase_pattern = r"\b" + r"\s+".join(re.escape(token) for token in keyword_tokens) + r"\b"
            if re.search(phrase_pattern, normalized_text):
                return True
            continue
        escaped_keyword = re.escape(keyword_tokens[0])
        if re.search(rf"\b{escaped_keyword}\b", normalized_text):
            return True
    return False


def _message_has_jira_mutation_intent(text: str) -> bool:
    return _message_has_any_keyword(text, _JIRA_MUTATION_INTENT_KEYWORDS)


def _message_has_jira_transform_intent(text: str) -> bool:
    return _message_has_any_keyword(text, _JIRA_TRANSFORM_INTENT_KEYWORDS)


def _message_has_jira_retrieval_intent(text: str, latest_user_message: str) -> bool:
    if _message_has_any_keyword(text, _JIRA_RETRIEVAL_HINTS):
        return True
    has_retrieval_verb = bool(re.search(r"\b(get|show|read|open|fetch|retrieve)\b", text))
    has_issue_hint = ("jira" in text) or ("issue" in text) or bool(
        re.search(r"\b[A-Z][A-Z0-9_]*-\d+\b", latest_user_message or "")
    )
    has_detail_hint = "detail" in text
    return has_retrieval_verb and (has_issue_hint or has_detail_hint)


def _has_mixed_intent(text: str) -> bool:
    has_connector = _message_has_any_keyword(text, _JIRA_SEQUENCE_CONNECTORS)
    return has_connector and _message_has_jira_mutation_intent(text)


def should_passthrough_tool_result(
    *,
    tool_name: str,
    tool_result: Any,
    latest_user_message: str,
    tool_calls_count: int,
) -> bool:
    """Conservative shortcut for direct Jira detail passthrough."""
    if tool_calls_count != 1 or tool_name not in _JIRA_DETAIL_TOOLS:
        return False
    if not getattr(tool_result, "success", False):
        return False
    content = (getattr(tool_result, "content", "") or "").strip()
    if not content:
        return False

    text = (latest_user_message or "").lower()
    if _message_has_any_keyword(text, _JIRA_SEQUENCE_WORDS):
        return False
    if _has_mixed_intent(text):
        return False
    if _message_has_jira_mutation_intent(text):
        return False
    if _message_has_jira_transform_intent(text):
        return False
    return _message_has_jira_retrieval_intent(text, latest_user_message)
