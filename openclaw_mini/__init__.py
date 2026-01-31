"""OpenClaw Mini - A simple version of OpenClaw written in Python."""

__version__ = "0.1.0"

from openclaw_mini.config import config
from openclaw_mini.agent import agent, llm_client
from openclaw_mini.gateway import gateway
from openclaw_mini.session import session_manager
from openclaw_mini.channel import discord_channel

__all__ = [
    "__version__",
    "config",
    "agent",
    "llm_client",
    "gateway",
    "session_manager",
    "discord_channel",
]
