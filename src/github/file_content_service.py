from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .source_manifest import format_github_source_manifest
from .source_service import prepare_github_file_source


@dataclass(frozen=True)
class _DefaultGitHubRef:
    owner: str
    repo: str
    path: str
    branch: str


async def render_github_file_manifest(
    owner: str,
    repo: str,
    path: str,
    branch: Optional[str] = None,
    *,
    session_id: str | None = None,
    preview: bool = True,
) -> str:
    default_ref = _DefaultGitHubRef(
        owner=str(owner or "").strip(),
        repo=str(repo or "").strip(),
        path="",
        branch=str(branch or "main").strip() or "main",
    )
    prepared = await prepare_github_file_source(str(path or "").strip(), default_ref, session_id=session_id)
    return format_github_source_manifest(prepared["bundle"], include_preview=preview)
