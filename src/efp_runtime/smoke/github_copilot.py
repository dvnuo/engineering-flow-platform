"""GitHub Copilot EFP runtime smoke entrypoint."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any, Sequence

from ..llm.models import DEFAULT_MODEL_ID, DEFAULT_PROVIDER_ID
from ..llm.provider import (
    GitHubCopilotProvider,
    ProviderTransportError,
    RecordingTransport,
    github_copilot_provider_from_env,
)
from ..loop import LoopStatus, RuntimeLoopResult, RuntimeLoopRunner
from ..session.models import MessagePartType
from ..session.store import InMemorySessionStore
from ..tools.registry import ToolRegistry
from ..tools.runtime import ToolRuntime


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return asyncio.run(_run(args))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a EFP runtime GitHub Copilot provider smoke check.",
    )
    parser.add_argument(
        "--prompt",
        default="Say ok",
        help="User prompt to send through the EFP runtime loop.",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL_ID,
        help="GitHub Copilot model id for the provider payload.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build and print the provider payload without network I/O.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=60,
        help="HTTP timeout in seconds for non-dry-run requests.",
    )
    return parser


async def _run(args: argparse.Namespace) -> int:
    if args.dry_run:
        transport = RecordingTransport([_dry_run_response()])
        provider = GitHubCopilotProvider(
            transport=transport,
            model=args.model,
        )
        result = await _run_loop(provider, prompt=args.prompt)
        payload = transport.payloads[0] if transport.payloads else {}
        _print_json(_dry_run_summary(provider=provider, payload=payload, result=result))
        return 0

    try:
        provider = github_copilot_provider_from_env(
            model=args.model,
            timeout=args.timeout,
        )
    except ProviderTransportError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    result = await _run_loop(provider, prompt=args.prompt)
    _print_json(_real_run_summary(provider=provider, result=result))
    if result.status != LoopStatus.COMPLETED:
        return 1
    return 0


async def _run_loop(
    provider: GitHubCopilotProvider,
    *,
    prompt: str,
) -> RuntimeLoopResult:
    runner = RuntimeLoopRunner(
        store=InMemorySessionStore(),
        provider=provider,
        tool_runtime=ToolRuntime(ToolRegistry()),
        max_iterations=1,
    )
    return await runner.run(
        session_id="github-copilot-smoke",
        user_text=prompt,
        metadata={"run_id": "github-copilot-smoke"},
    )


def _dry_run_summary(
    *,
    provider: GitHubCopilotProvider,
    payload: dict[str, Any],
    result: RuntimeLoopResult,
) -> dict[str, Any]:
    return {
        "dry_run": True,
        "provider": DEFAULT_PROVIDER_ID,
        "provider_id": provider.metadata.get("provider_id"),
        "model": provider.model,
        "status": result.status,
        "payload": _compact_payload(payload),
        "payload_summary": _payload_summary(payload),
    }


def _real_run_summary(
    *,
    provider: GitHubCopilotProvider,
    result: RuntimeLoopResult,
) -> dict[str, Any]:
    return {
        "dry_run": False,
        "provider": DEFAULT_PROVIDER_ID,
        "provider_id": provider.metadata.get("provider_id"),
        "model": provider.model,
        "status": result.status,
        "iterations": result.iterations,
        "assistant_text": _assistant_text(result),
        "usage": result.usage,
    }


def _payload_summary(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "model": payload.get("model"),
        "message_count": len(payload.get("messages", [])),
        "tool_count": len(payload.get("tools", [])),
        "stream": payload.get("stream"),
        "metadata": _compact_metadata(payload.get("metadata", {})),
    }


def _compact_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "model": payload.get("model"),
        "messages": payload.get("messages", []),
        "tools": payload.get("tools", []),
        "stream": payload.get("stream"),
        "metadata": _compact_metadata(payload.get("metadata", {})),
    }


def _compact_metadata(metadata: Any) -> dict[str, Any]:
    if not isinstance(metadata, dict):
        return {}
    projection = metadata.get("efp_projection")
    projection_summary: dict[str, Any] = {}
    if isinstance(projection, dict):
        projection_summary = {
            "endpoint": projection.get("endpoint"),
            "message_count": len(projection.get("messages", [])),
            "tool_count": len(projection.get("tools", [])),
        }
    return {
        "provider": metadata.get("provider"),
        "provider_id": metadata.get("provider_id"),
        "model_id": metadata.get("model_id"),
        "session_id": metadata.get("session_id"),
        "iteration": metadata.get("iteration"),
        "max_iterations": metadata.get("max_iterations"),
        "track_usage": metadata.get("track_usage"),
        "efp_projection": projection_summary,
    }


def _assistant_text(result: RuntimeLoopResult) -> str:
    message = result.final_assistant_message
    if message is None:
        return ""
    texts = []
    for part in message.parts:
        if part.type in {MessagePartType.TEXT, MessagePartType.ERROR} and part.text:
            texts.append(part.text)
    return "\n".join(texts)


def _dry_run_response() -> dict[str, Any]:
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "dry-run ok",
                },
                "finish_reason": "stop",
            }
        ]
    }


def _print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(main())
