#!/usr/bin/env python3
"""OpenClaw Mini - A simple version of OpenClaw written in Python."""

import asyncio
import logging
import sys
from pathlib import Path

# Get the directory containing this script
script_dir = Path(__file__).resolve().parent
project_root = script_dir.parent

# Add both script directory and project root to Python path
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))
if str(script_dir) not in sys.path:
    sys.path.insert(0, str(script_dir))

from gateway.server import gateway
from config import config
from session.persistence import session_store
from session.usage import usage_tracker


def setup_logging(level: int = logging.INFO) -> None:
    """Configure logging."""
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )


def check_config() -> tuple[bool, list[str]]:
    """Check configuration and return (can_start, warnings).
    
    Returns:
        tuple of (can_start, list of warning messages)
    """
    warnings = []
    can_start = True
    
    # Check Discord configuration
    discord_token = config.discord.get("bot_token")
    if not discord_token:
        warnings.append("Discord bot_token not configured (Discord channel will be disabled)")
    
    # Check LLM configuration
    llm_api_key = config.llm.get("api_key")
    if not llm_api_key:
        warnings.append("LLM api_key not configured (Agent will not respond to messages)")
        can_start = False
    
    # Check if any channel is configured
    jira_enabled = config.jira.get("enabled")
    if not discord_token and not jira_enabled:
        warnings.append("No messaging channel configured (Discord or Jira)")
    
    return can_start, warnings


async def main() -> None:
    """Main entry point."""
    setup_logging()

    logger = logging.getLogger(__name__)
    logger.info("Starting OpenClaw Mini...")

    # Check configuration
    can_start, warnings = check_config()
    
    for warning in warnings:
        logger.warning(warning)
    
    if not can_start:
        logger.error("Cannot start: LLM api_key is required")
        return

    # Initialize session store and usage tracker
    try:
        await session_store.ensure_dir()
        logger.info("Session store initialized")
        await usage_tracker.ensure_dir()
        logger.info("Usage tracker initialized")
    except Exception as e:
        logger.warning(f"Failed to initialize session/usage tracking: {e}")

    try:
        await gateway.start()
        logger.info("OpenClaw Mini is running. Press Ctrl+C to stop.")

        # Keep running
        while True:
            await asyncio.sleep(1)

    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        await gateway.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nShutdown complete.")
