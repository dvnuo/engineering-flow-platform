"""Channel package for OpsClaw Mini."""

from .discord import discord_channel, DiscordChannel
from .jira import (
    jira_channel, 
    JiraChannel,
    jira_get_issue,
    jira_search,
    jira_add_comment,
    jira_create_issue,
    jira_transition,
    jira_get_transitions,
    jira_get_comments,  # New: Get comments tool
)

__all__ = [
    "discord_channel", 
    "DiscordChannel", 
    "jira_channel", 
    "JiraChannel",
    "jira_get_issue",
    "jira_search",
    "jira_add_comment",
    "jira_create_issue",
    "jira_transition",
    "jira_get_transitions",
    "jira_get_comments",
]
