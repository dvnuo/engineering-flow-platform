"""Confluence adapter backed by the external confluence CLI."""

from __future__ import annotations

from src.external_cli.runner import run_json


async def add_comment(page_id: str, comment: str) -> dict:
    return await run_json(
        ["confluence", "page", "comment", "add", "--id", str(page_id), "--body-stdin", "--json"],
        input_text=str(comment),
    )
