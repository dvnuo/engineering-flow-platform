"""Small subprocess helpers for external integration CLIs."""

from __future__ import annotations

import asyncio
import json
import os
from typing import Mapping, Sequence


class ExternalCLIError(RuntimeError):
    """Raised when an external CLI command cannot complete."""


def _merged_env(env: Mapping[str, str] | None) -> dict[str, str] | None:
    if not env:
        return None
    merged = os.environ.copy()
    merged.update({str(k): str(v) for k, v in env.items()})
    return merged


async def run_text(
    args: Sequence[str],
    *,
    input_text: str | None = None,
    env: Mapping[str, str] | None = None,
) -> str:
    """Run a CLI command and return stdout text.

    Secrets should be passed through config files or stdin rather than command
    arguments. Error messages include command names and stderr, never env.
    """

    command = [str(arg) for arg in args]
    if not command:
        raise ExternalCLIError("external CLI command is required")
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE if input_text is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_merged_env(env),
        )
    except FileNotFoundError as exc:
        raise ExternalCLIError(f"Required external CLI is not installed: {command[0]}") from exc

    stdout, stderr = await process.communicate(input_text.encode("utf-8") if input_text is not None else None)
    stdout_text = stdout.decode("utf-8", errors="replace")
    stderr_text = stderr.decode("utf-8", errors="replace").strip()
    if process.returncode != 0:
        detail = stderr_text or stdout_text.strip() or f"{command[0]} exited with status {process.returncode}"
        raise ExternalCLIError(f"{command[0]} command failed: {detail}")
    return stdout_text


async def run_json(
    args: Sequence[str],
    *,
    input_text: str | None = None,
    env: Mapping[str, str] | None = None,
) -> dict:
    """Run a CLI command and parse stdout as a JSON object."""

    stdout_text = await run_text(args, input_text=input_text, env=env)
    if not stdout_text.strip():
        return {}
    try:
        parsed = json.loads(stdout_text)
    except json.JSONDecodeError as exc:
        raise ExternalCLIError(f"{args[0]} command returned non-JSON output") from exc
    if not isinstance(parsed, dict):
        raise ExternalCLIError(f"{args[0]} command returned JSON {type(parsed).__name__}, expected object")
    return parsed
