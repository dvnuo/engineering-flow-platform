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


def setup_logging(level: int = logging.INFO) -> None:
    """Configure logging."""
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )


async def main() -> None:
    """Main entry point."""
    setup_logging()

    logger = logging.getLogger(__name__)
    logger.info("Starting OpenClaw Mini...")

    # Check configuration
    if not config.discord.get("bot_token"):
        logger.error("Discord bot_token not configured in config.yaml")
        return

    if not config.llm.get("api_key"):
        logger.error("LLM api_key not configured in config.yaml")
        return

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
