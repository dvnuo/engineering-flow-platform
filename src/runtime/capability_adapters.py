"""Lightweight adapter capability descriptors for runtime surfaces."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class AdapterActionDescriptor:
    action_id: str
    adapter: str
    name: str
    input_schema: Dict[str, Any] = field(default_factory=dict)
    output_schema: Dict[str, Any] = field(default_factory=dict)
    policy_tags: List[str] = field(default_factory=list)
    requires_identity_binding: bool = False
    enabled: bool = True
    source_ref: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


def build_github_adapter_capabilities() -> List[AdapterActionDescriptor]:
    return [
        AdapterActionDescriptor(
            action_id="adapter:github:review_pull_request",
            adapter="github",
            name="review_pull_request",
            input_schema={
                "type": "object",
                "properties": {
                    "owner": {"type": "string"},
                    "repo": {"type": "string"},
                    "pull_number": {"type": "integer"},
                },
                "required": ["owner", "repo", "pull_number"],
            },
            output_schema={"type": "object", "properties": {"summary": {"type": "string"}}},
            policy_tags=["github", "read", "review"],
            requires_identity_binding=True,
            source_ref="src.github",
        ),
        AdapterActionDescriptor(
            action_id="adapter:github:add_comment",
            adapter="github",
            name="add_comment",
            input_schema={"type": "object", "properties": {"owner": {"type": "string"}, "repo": {"type": "string"}, "issue_number": {"type": "integer"}, "comment": {"type": "string"}}},
            output_schema={"type": "object", "properties": {"status": {"type": "string"}}},
            policy_tags=["github", "write", "comment"],
            requires_identity_binding=True,
            source_ref="src.github",
        ),
    ]


def build_jira_adapter_capabilities() -> List[AdapterActionDescriptor]:
    return [
        AdapterActionDescriptor(
            action_id="adapter:jira:read_issue",
            adapter="jira",
            name="read_issue",
            input_schema={"type": "object", "properties": {"issue_key": {"type": "string"}}, "required": ["issue_key"]},
            output_schema={"type": "object", "properties": {"issue": {"type": "string"}}},
            policy_tags=["jira", "read"],
            requires_identity_binding=True,
            source_ref="src.jira",
        ),
        AdapterActionDescriptor(
            action_id="adapter:jira:update_issue",
            adapter="jira",
            name="update_issue",
            input_schema={"type": "object", "properties": {"issue_key": {"type": "string"}, "fields": {"type": "object"}}, "required": ["issue_key"]},
            output_schema={"type": "object", "properties": {"status": {"type": "string"}}},
            policy_tags=["jira", "write", "update"],
            requires_identity_binding=True,
            source_ref="src.jira",
        ),
        AdapterActionDescriptor(
            action_id="adapter:jira:assign_issue",
            adapter="jira",
            name="assign_issue",
            input_schema={"type": "object", "properties": {"issue_key": {"type": "string"}, "assignee": {"type": "string"}}},
            output_schema={"type": "object", "properties": {"status": {"type": "string"}}},
            policy_tags=["jira", "write", "assign"],
            requires_identity_binding=True,
            source_ref="src.jira",
        ),
        AdapterActionDescriptor(
            action_id="adapter:jira:transition_issue",
            adapter="jira",
            name="transition_issue",
            input_schema={"type": "object", "properties": {"issue_key": {"type": "string"}, "transition": {"type": "string"}}},
            output_schema={"type": "object", "properties": {"status": {"type": "string"}}},
            policy_tags=["jira", "write", "transition"],
            requires_identity_binding=True,
            source_ref="src.jira",
        ),
        AdapterActionDescriptor(
            action_id="adapter:jira:add_comment",
            adapter="jira",
            name="add_comment",
            input_schema={
                "type": "object",
                "properties": {
                    "issue_key": {"type": "string"},
                    "comment": {"type": "string"},
                },
                "required": ["issue_key", "comment"],
            },
            output_schema={"type": "object", "properties": {"status": {"type": "string"}}},
            policy_tags=["jira", "write", "comment"],
            requires_identity_binding=True,
            source_ref="src.jira",
        ),
    ]
