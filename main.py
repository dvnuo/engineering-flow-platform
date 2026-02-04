#!/usr/bin/env python3
"""OpsClaw Mini - A simple version of OpsClaw written in Python."""

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
from session.manager import session_manager
from session.usage import usage_tracker
from cron.mention_poller import start_polling, stop_polling, is_enabled
from skills.git.skill import setup_ssh_key, setup_git_user


def setup_logging(level: int = None) -> None:
    """Configure logging.
    
    Args:
        level: Logging level. If None, reads from config.debug.log_level.
    """
    # Determine log level from config if not specified
    if level is None:
        log_level_str = config.debug.get("log_level", "INFO").upper()
        level = getattr(logging, log_level_str, logging.INFO)
    
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
    logger.info("Starting OpsClaw Mini...")

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
        await session_manager.initialize()
        logger.info("Session manager initialized")
    except Exception as e:
        logger.warning(f"Failed to initialize session/usage tracking: {e}")

    # Setup SSH key for git operations (from config)
    try:
        ssh_key_configured = await setup_ssh_key()
        if ssh_key_configured:
            logger.info("SSH key configured successfully")
        else:
            logger.debug("SSH key not configured (ssh.enabled=false or no key path)")
    except Exception as e:
        logger.warning(f"Failed to setup SSH key: {e}")

    # Setup git user configuration (from config)
    try:
        git_user_configured = await setup_git_user()
        if git_user_configured:
            logger.info("Git user configured successfully")
        else:
            logger.debug("Git user not configured (git.user.name/email not set)")
    except Exception as e:
        logger.warning(f"Failed to setup git user: {e}")

    try:
        await gateway.start()
        
        # Start mention polling if enabled
        polling_task = None
        if is_enabled():
            logger.info("Starting mention polling...")
            polling_task = asyncio.create_task(start_polling())
        
        logger.info("OpsClaw Mini is running. Press Ctrl+C to stop.")

        # Keep running
        while True:
            await asyncio.sleep(1)

    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        # Stop mention polling
        if polling_task and not polling_task.done():
            logger.info("Stopping mention polling...")
            await stop_polling()
            await polling_task
        
        await gateway.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nShutdown complete.")
