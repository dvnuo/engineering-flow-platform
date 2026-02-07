"""Engineering Flow Platform - A simple version of Engineering Flow Platform written in Python."""

__version__ = "0.1.0"

from .config import config
from .agent import agent, llm_client
from .gateway import gateway
from .session import session_manager
from .channel import discord_channel

__all__ = [
    "__version__",
    "config",
    "agent",
    "llm_client",
    "gateway",
    "session_manager",
    "discord_channel",
]
