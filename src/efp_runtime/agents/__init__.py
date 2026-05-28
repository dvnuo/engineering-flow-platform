"""Runtime v2 agent profiles and subagent task runners."""

from .background_tasks import BackgroundTaskManager, BackgroundTaskRecord
from .discovery import (
    DEFAULT_AGENT_DIRECTORIES,
    MarkdownAgentDocument,
    discover_agent_profiles,
    load_agent_registry,
    load_markdown_agent_document,
)
from .defaults import DEFAULT_AGENT_PROFILE_NAMES, default_agent_profiles
from .profile import AgentProfile
from .registry import AgentRegistry
from .task_runner import (
    SubagentRunResult,
    create_agent_task_tool,
    create_agent_task_tools,
    create_subagent_task_runner,
)

__all__ = [
    "AgentProfile",
    "AgentRegistry",
    "BackgroundTaskManager",
    "BackgroundTaskRecord",
    "DEFAULT_AGENT_DIRECTORIES",
    "DEFAULT_AGENT_PROFILE_NAMES",
    "MarkdownAgentDocument",
    "SubagentRunResult",
    "create_agent_task_tool",
    "create_agent_task_tools",
    "create_subagent_task_runner",
    "default_agent_profiles",
    "discover_agent_profiles",
    "load_agent_registry",
    "load_markdown_agent_document",
]
