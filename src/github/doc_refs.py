from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from src.github import github_channel
from src.github.url_utils import normalize_github_api_base_url


@dataclass(frozen=True)
class GitHubDocRef:
    owner: str
    repo: str
    branch: str
    path: str


def _allowed_github_hosts() -> set[str]:
    hosts = {"github.com"}
    base_url = str(getattr(github_channel, "base_url", "") or "").strip()
    if base_url:
        parsed_base_url = urlparse(normalize_github_api_base_url(base_url))
        if parsed_base_url.netloc:
            hosts.add(parsed_base_url.netloc.lower())
    return hosts


def parse_github_doc_ref(raw: str, default_ref) -> GitHubDocRef:
    normalized = str(raw or "").strip()
    if not normalized:
        raise ValueError("github_doc_ref is required")

    if normalized.startswith("http://") or normalized.startswith("https://"):
        parsed = urlparse(normalized)
        if parsed.netloc.lower() not in _allowed_github_hosts():
            raise ValueError(f"Unsupported GitHub doc URL host: {parsed.netloc}")
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) < 5 or parts[2] != "blob":
            raise ValueError("GitHub doc URL must be in /owner/repo/blob/<branch>/<path> format")
        return GitHubDocRef(owner=parts[0], repo=parts[1], branch=parts[3], path="/".join(parts[4:]).strip("/"))

    return GitHubDocRef(
        owner=default_ref.owner,
        repo=default_ref.repo,
        branch=default_ref.branch,
        path=normalized.strip("/"),
    )
