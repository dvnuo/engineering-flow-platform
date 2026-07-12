#!/usr/bin/env python3
"""Engineering Flow Platform - A simple version of Engineering Flow Platform written in Python."""

from __future__ import annotations

import asyncio
import logging
import shutil
import sys
from pathlib import Path

from ruamel.yaml import YAML

# Get the directory containing this script
script_dir = Path(__file__).resolve().parent
project_root = script_dir.parent

# Add both script directory and project root to Python path
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))
if str(script_dir) not in sys.path:
    sys.path.insert(0, str(script_dir))

from src.config import bootstrap_profile_boot, config
from src.workspace_defaults import resolve_runtime_workspace

# Project the EFP_PROFILE_CONFIG overlay exactly once: external CLI projection,
# tools config env-var export, proxy/jenkins/mobile env, then scrub the profile blob.
# Must run before importing src.gateway.server (Gateway() executes at import).
bootstrap_profile_boot()

from src.efp_runtime.session.gateway_facade import runtime_session_manager
from src.gateway.server import gateway
from src.sessions.persistence import session_persistence
from src.sessions.usage import usage_tracker
from src.cron.jira_reconciliation import start_reconciliation, stop_reconciliation, is_enabled as is_jira_reconciliation_enabled
from src.git.api import setup_git_user
from src.utils.logger import setup_logging, get_logger


def setup_logging_config() -> logging.Logger:
    """Configure comprehensive logging with detailed output.
    
    Returns:
        Logger instance
    """
    # Determine log level from config
    log_level_str = config.debug.get("log_level", "INFO").upper()
    
    # Setup enhanced logging
    logger = setup_logging(
        level=log_level_str,
        log_dir="logs",
        log_file="efp.log",
        max_size_mb=10,
        backup_count=5
    )
    
    return logger


def check_config() -> tuple[bool, list[str]]:
    """Check configuration and return (can_start, warnings).
    
    Returns:
        tuple of (can_start, list of warning messages)
    """
    warnings = []
    can_start = True
    
    # Check LLM configuration
    llm_api_key = config.llm.get("api_key")
    if not llm_api_key:
        warnings.append("LLM api_key is not configured (Agent will not respond to messages)")
        # Allow startup without api_key - user can configure via webchat settings
        # can_start = False  # Disabled: allow startup without api_key
    
    return can_start, warnings


def initialize_workspace(logger: logging.Logger) -> Path:
    """Ensure the default workspace exists and copy example markdown files from the
    repository `workspace/` folder into it (rename *.md.example -> *.md).

    Behavior:
    - Creates the default runtime workspace if it doesn't exist.
    - Copies files matching `workspace/*.md.example` from the project root
      into the default runtime workspace and strips the `.example` suffix.
    - Does not overwrite existing files (skips if destination exists).

    Returns:
        Path to the workspace directory.
    """
    try:
        config_data = config.get_effective_config()
    except Exception:
        config_data = config._config
    default_workspace = resolve_runtime_workspace(config_data)
    try:
        # Ensure the directory exists
        default_workspace.mkdir(parents=True, exist_ok=True)
        logger.info(f"Workspace directory ready | path={default_workspace}")

        # Source directory inside project (project_root/workspace)
        source_dir = Path(__file__).parent / "workspace"
        if not source_dir.exists() or not source_dir.is_dir():
            logger.debug(f"No source workspace directory found to copy examples from | path={source_dir}")
            return default_workspace

        # Find *.md.example files and copy them
        copied = 0
        for example_file in source_dir.glob("*.md.example"):
            # Strip the trailing `.example` suffix to produce the target filename
            if example_file.name.endswith('.example'):
                target_name = example_file.name[:-8]
            else:
                target_name = example_file.name

            dest = default_workspace / target_name
            if dest.exists():
                logger.debug(f"Skipping existing workspace file | path={dest}")
                continue

            try:
                shutil.copy2(example_file, dest)
                copied += 1
                logger.info(f"Copied workspace example -> {dest}")
            except Exception as e:
                logger.warning(f"Failed to copy {example_file} -> {dest} | error={e}", exc_info=True)

        if copied == 0:
            logger.debug("No workspace example files copied (none found or already present)")
        else:
            logger.info(f"Workspace initialized with {copied} example file(s)")

    except Exception as e:
        logger.error(f"Failed to initialize workspace directory | error={e}", exc_info=True)

    return default_workspace


async def _shutdown_jira_reconciliation_task(
    jira_reconciliation_task: asyncio.Task | None,
    logger: logging.Logger,
) -> None:
    if not jira_reconciliation_task or jira_reconciliation_task.done():
        return
    logger.info("Stopping Jira reconciliation...")
    await stop_reconciliation()
    jira_reconciliation_task.cancel()
    try:
        await jira_reconciliation_task
    except asyncio.CancelledError:
        pass
    logger.info("Jira reconciliation stopped")


async def main() -> None:
    """Main entry point."""
    import argparse
    import logging
    
    # Parse command line arguments
    parser = argparse.ArgumentParser(description="Engineering Flow Platform")
    parser.add_argument("--httpx-trace", action="store_true", 
                        help="Enable httpx detailed trace logging (very verbose)")
    parser.add_argument("--debug", action="store_true",
                        help="Enable debug logging")
    args, _ = parser.parse_known_args()
    
    # Apply command line arguments to config
    if args.debug:
        config._config["debug"] = {"enabled": True, "httpx_trace": args.httpx_trace}
    elif args.httpx_trace:
        current_debug = config._config.get("debug", {})
        current_debug["httpx_trace"] = True
        config._config["debug"] = current_debug
    
    # Setup httpx logging level BEFORE setup_logging
    httpx_logger = logging.getLogger("httpx")
    httpcore_logger = logging.getLogger("httpcore")
    
    # Check if httpx trace should be enabled
    debug_config = config._config.get("debug", {})
    httpx_trace_enabled = debug_config.get("httpx_trace", False) or args.httpx_trace
    
    if httpx_trace_enabled:
        httpx_logger.setLevel(logging.DEBUG)
        httpcore_logger.setLevel(logging.DEBUG)
    else:
        httpx_logger.setLevel(logging.WARNING)
        httpcore_logger.setLevel(logging.WARNING)
    
    logger = setup_logging_config()
    logger.info("=" * 60)
    logger.info("Engineering Flow Platform - Starting...")
    logger.info(f"Python version: {sys.version}")
    logger.info(f"Working directory: {Path.cwd()}")
    
    # Check configuration
    can_start, warnings = check_config()
    
    logger.info(f"Proxy enabled: {config.proxy.get('enabled', False)}")
    
    for warning in warnings:
        logger.warning(warning)
    
    if not can_start:
        logger.error("Cannot start: LLM api_key is required")
        return

    logger.info("Configuration check passed")

    # Initialize workspace
    initialize_workspace(logger)

    # Initialize session store and usage tracker
    try:
        # Directory already created in __init__
        logger.info(f"Session store initialized | path={session_persistence.storage_dir}")
        logger.info(f"Usage tracker initialized | path={usage_tracker.base_path}")
        await runtime_session_manager.initialize()
        logger.info("Runtime session manager initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize session/usage tracking | error={e}", exc_info=True)

    # Setup git user configuration (from config)
    try:
        git_user_configured = await setup_git_user()
        if git_user_configured:
            logger.info("Git user configured successfully")
        else:
            logger.debug("Git user not configured (git.user.name/email not set)")
    except Exception as e:
        logger.warning(f"Failed to setup git user | error={e}", exc_info=True)

    jira_reconciliation_task = None
    
    try:
        await gateway.start()
        logger.info("Gateway server started")
        
        if is_jira_reconciliation_enabled():
            logger.info("Starting Jira reconciliation...")
            jira_reconciliation_task = asyncio.create_task(start_reconciliation())
            logger.info("Jira reconciliation started")
        else:
            logger.debug("Jira reconciliation is disabled")
        
        logger.info("=" * 60)
        logger.info("Engineering Flow Platform is running")
        logger.info("Press Ctrl+C to stop")
        logger.info("=" * 60)

        # Keep running
        while True:
            await asyncio.sleep(1)

    except KeyboardInterrupt:
        logger.info("Shutdown signal received")
    except Exception as e:
        logger.error(f"Unexpected error in main loop | error={e}", exc_info=True)
    finally:
        await _shutdown_jira_reconciliation_task(jira_reconciliation_task, logger)
        
        try:
            await gateway.stop()
            logger.info("Gateway server stopped")
        except Exception as e:
            logger.error(f"Error stopping gateway | error={e}", exc_info=True)
        
        logger.info("Engineering Flow Platform shutdown complete")
        logger.info("=" * 60)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nShutdown complete.")
    except Exception as e:
        print(f"\nFatal error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
