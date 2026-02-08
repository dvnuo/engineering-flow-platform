"""Engineering Flow Platform - A simple version of Engineering Flow Platform written in Python."""

__version__ = "0.1.0"

from .src.config import config
from .src.agents.core import agent
from .src.agents.llm import llm_client
from .src.gateway.server import gateway
from .src.sessions.manager import session_manager
from .src.channels.discord import discord_channel

__all__ = [
    "__version__",
    "config",
    "agent",
    "llm_client",
    "gateway",
    "session_manager",
    "discord_channel",
]
