from __future__ import annotations

import base64
import io
from dataclasses import dataclass
from typing import Any, Dict, Tuple
from urllib.parse import urlparse

from ruamel.yaml import YAML

from src.github import github_channel

_yaml = YAML()
_yaml.default_flow_style = False


@dataclass(frozen=True)
class BundleRef:
    owner: str
    repo: str
    path: str
    branch: str

    @property
    def repo_full_name(self) -> str:
        return f"{self.owner}/{self.repo}"


class RequirementBundleError(ValueError):
    """Validation or IO error for requirement bundle assets."""


@dataclass(frozen=True)
class GitHubDocRef:
    owner: str
    repo: str
    branch: str
    path: str


def _allowed_github_hosts() -> set[str]:
    hosts = {"github.com"}

    hostname = str(getattr(github_channel, "hostname", "") or "").strip().lower()
    if hostname:
        hosts.add(hostname)

    base_url = str(getattr(github_channel, "base_url", "") or "").strip()
    if base_url:
        normalized_base_url = base_url if "://" in base_url else f"https://{base_url.lstrip('/')}"
        parsed_base_url = urlparse(normalized_base_url)
        if parsed_base_url.netloc:
            hosts.add(parsed_base_url.netloc.lower())

    return hosts


def parse_bundle_ref(bundle_ref: Dict[str, Any]) -> BundleRef:
    if not isinstance(bundle_ref, dict):
        raise RequirementBundleError("bundle_ref must be an object")

    repo_full = str(bundle_ref.get("repo") or "").strip()
    path = str(bundle_ref.get("path") or "").strip().strip("/")
    branch = str(bundle_ref.get("branch") or "").strip()

    if not repo_full or "/" not in repo_full:
        raise RequirementBundleError("bundle_ref.repo must be in 'owner/repo' format")
    owner, repo = repo_full.split("/", 1)
    if not owner or not repo:
        raise RequirementBundleError("bundle_ref.repo must be in 'owner/repo' format")
    if not path:
        raise RequirementBundleError("bundle_ref.path is required")
    if not branch:
        raise RequirementBundleError("bundle_ref.branch is required")

    return BundleRef(owner=owner, repo=repo, path=path, branch=branch)


async def read_bundle_text(ref: BundleRef, relative_file: str) -> str:
    file_path = f"{ref.path}/{relative_file}".strip("/")
    return await read_repo_text(ref, file_path)


async def read_repo_text(ref: BundleRef, repo_relative_file: str) -> str:
    file_path = str(repo_relative_file or "").strip().strip("/")
    if not file_path:
        raise RequirementBundleError("repo_relative_file is required")
    file_data = await github_channel.get_file(ref.owner, ref.repo, file_path, ref.branch)
    content = file_data.get("content")
    if not isinstance(content, str) or not content.strip():
        raise RequirementBundleError(f"File not found or empty: {file_path}")
    try:
        return base64.b64decode(content).decode("utf-8")
    except Exception as exc:  # pragma: no cover - defensive
        raise RequirementBundleError(f"Failed to decode file content: {file_path}") from exc


def parse_github_doc_ref(raw: str, default_ref: BundleRef) -> GitHubDocRef:
    normalized = str(raw or "").strip()
    if not normalized:
        raise RequirementBundleError("github_doc_ref is required")

    if normalized.startswith("http://") or normalized.startswith("https://"):
        parsed = urlparse(normalized)
        if parsed.netloc.lower() not in _allowed_github_hosts():
            raise RequirementBundleError(f"Unsupported GitHub doc URL host: {parsed.netloc}")
        parts = [part for part in parsed.path.split("/") if part]
        # /owner/repo/blob/branch/path/to/file
        if len(parts) < 5 or parts[2] != "blob":
            raise RequirementBundleError("GitHub doc URL must be in /owner/repo/blob/<branch>/<path> format")
        owner = parts[0]
        repo = parts[1]
        branch = parts[3]
        path = "/".join(parts[4:]).strip("/")
        if not owner or not repo or not branch or not path:
            raise RequirementBundleError("GitHub doc URL is missing owner/repo/branch/path")
        return GitHubDocRef(owner=owner, repo=repo, branch=branch, path=path)

    return GitHubDocRef(
        owner=default_ref.owner,
        repo=default_ref.repo,
        branch=default_ref.branch,
        path=normalized.strip("/"),
    )


async def read_github_doc_text(raw: str, default_ref: BundleRef) -> tuple[GitHubDocRef, str]:
    doc_ref = parse_github_doc_ref(raw, default_ref)
    file_data = await github_channel.get_file(doc_ref.owner, doc_ref.repo, doc_ref.path, doc_ref.branch)
    content = file_data.get("content")
    if not isinstance(content, str) or not content.strip():
        raise RequirementBundleError(f"File not found or empty: {doc_ref.owner}/{doc_ref.repo}/{doc_ref.path}@{doc_ref.branch}")
    try:
        decoded = base64.b64decode(content).decode("utf-8")
    except Exception as exc:  # pragma: no cover - defensive
        raise RequirementBundleError(
            f"Failed to decode file content: {doc_ref.owner}/{doc_ref.repo}/{doc_ref.path}@{doc_ref.branch}"
        ) from exc
    return doc_ref, decoded


async def read_bundle_yaml(ref: BundleRef, relative_file: str) -> Dict[str, Any]:
    raw = await read_bundle_text(ref, relative_file)
    parsed = _yaml.load(raw)
    if parsed is None:
        return {}
    if not isinstance(parsed, dict):
        raise RequirementBundleError(f"YAML document must be an object: {relative_file}")
    return dict(parsed)


async def load_bundle_manifest(bundle_ref: Dict[str, Any]) -> Tuple[BundleRef, Dict[str, Any]]:
    ref = parse_bundle_ref(bundle_ref)
    manifest = await read_bundle_yaml(ref, "bundle.yaml")
    validate_bundle_manifest(manifest)
    return ref, manifest


def resolve_bundle_links(manifest: Dict[str, Any]) -> tuple[str, str]:
    links = manifest.get("links")
    if not isinstance(links, dict):
        raise RequirementBundleError("bundle.yaml field 'links' must be an object")

    requirements_file = str(links.get("requirements_file") or "").strip().strip("/")
    test_cases_file = str(links.get("test_cases_file") or "").strip().strip("/")

    if not requirements_file:
        raise RequirementBundleError("bundle.yaml field 'links.requirements_file' must be a non-empty string")
    if not test_cases_file:
        raise RequirementBundleError("bundle.yaml field 'links.test_cases_file' must be a non-empty string")

    return requirements_file, test_cases_file


def resolve_target_bundle_ref(input_ref: BundleRef, manifest: Dict[str, Any]) -> BundleRef:
    storage = manifest.get("storage")
    if storage is None:
        storage = {}
    if not isinstance(storage, dict):
        raise RequirementBundleError("bundle.yaml field 'storage' must be an object")

    repo_full = str(storage.get("repo") or input_ref.repo_full_name).strip()
    path = str(storage.get("path") or input_ref.path).strip().strip("/")
    branch = str(storage.get("working_branch") or input_ref.branch).strip()

    return parse_bundle_ref({"repo": repo_full, "path": path, "branch": branch})


async def load_requirements_doc_for_ref(ref: BundleRef, requirements_file: str = "requirements.yaml") -> Dict[str, Any]:
    requirements = await read_bundle_yaml(ref, requirements_file)
    validate_requirements_doc(requirements)
    return requirements


async def load_requirements_doc(bundle_ref: Dict[str, Any]) -> Tuple[BundleRef, Dict[str, Any]]:
    ref = parse_bundle_ref(bundle_ref)
    requirements = await load_requirements_doc_for_ref(ref)
    return ref, requirements


async def write_bundle_yaml(ref: BundleRef, relative_file: str, payload: Dict[str, Any], commit_message: str) -> Dict[str, Any]:
    stream = io.StringIO()
    _yaml.dump(payload, stream)
    file_path = f"{ref.path}/{relative_file}".strip("/")
    return await github_channel.create_or_update_file(
        ref.owner,
        ref.repo,
        file_path,
        stream.getvalue(),
        commit_message,
        branch=ref.branch,
    )


async def write_requirements_doc(bundle_ref: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
    ref = parse_bundle_ref(bundle_ref)
    return await write_requirements_doc_for_ref(ref, payload)


async def write_requirements_doc_for_ref(
    ref: BundleRef, payload: Dict[str, Any], requirements_file: str = "requirements.yaml"
) -> Dict[str, Any]:
    return await write_bundle_yaml(
        ref,
        requirements_file,
        payload,
        f"chore(requirement-bundle): update {requirements_file} for {ref.path}",
    )


async def write_test_cases_doc(bundle_ref: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
    ref = parse_bundle_ref(bundle_ref)
    return await write_test_cases_doc_for_ref(ref, payload)


async def write_test_cases_doc_for_ref(
    ref: BundleRef, payload: Dict[str, Any], test_cases_file: str = "test-cases.yaml"
) -> Dict[str, Any]:
    return await write_bundle_yaml(
        ref,
        test_cases_file,
        payload,
        f"chore(requirement-bundle): update {test_cases_file} for {ref.path}",
    )


def build_test_design_context(bundle_manifest: Dict[str, Any], requirements_doc: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "bundle_id": bundle_manifest.get("bundle_id"),
        "title": bundle_manifest.get("title"),
        "scope": bundle_manifest.get("scope", {}),
        "summary": requirements_doc.get("summary", {}),
        "functional_requirements": requirements_doc.get("functional_requirements", []),
        "business_rules": requirements_doc.get("business_rules", []),
        "acceptance_criteria": requirements_doc.get("acceptance_criteria", []),
        "edge_cases": requirements_doc.get("edge_cases", []),
        "quality_flags": requirements_doc.get(
            "quality_flags",
            {"ambiguities": [], "conflicts": [], "missing_information": []},
        ),
    }


def validate_bundle_manifest(manifest: Dict[str, Any]) -> None:
    if not isinstance(manifest, dict):
        raise RequirementBundleError("bundle.yaml must be an object")

    required_top_level = ("bundle_id", "title", "status", "scope", "storage", "links")
    for key in required_top_level:
        if key not in manifest:
            raise RequirementBundleError(f"bundle.yaml missing required field: {key}")
    for key in ("bundle_id", "title", "status"):
        value = manifest.get(key)
        if not isinstance(value, str) or not value.strip():
            raise RequirementBundleError(f"bundle.yaml field '{key}' must be a non-empty string")

    scope = manifest.get("scope")
    if not isinstance(scope, dict):
        raise RequirementBundleError("bundle.yaml field 'scope' must be an object")
    for key in ("domain", "summary"):
        if key not in scope:
            raise RequirementBundleError(f"bundle.yaml missing required field: scope.{key}")
        value = scope.get(key)
        if not isinstance(value, str) or not value.strip():
            raise RequirementBundleError(f"bundle.yaml field 'scope.{key}' must be a non-empty string")

    storage = manifest.get("storage")
    if not isinstance(storage, dict):
        raise RequirementBundleError("bundle.yaml field 'storage' must be an object")
    for key in ("repo", "path", "base_branch", "working_branch"):
        if key not in storage:
            raise RequirementBundleError(f"bundle.yaml missing required field: storage.{key}")
        value = storage.get(key)
        if not isinstance(value, str) or not value.strip():
            raise RequirementBundleError(f"bundle.yaml field 'storage.{key}' must be a non-empty string")
    repo_full = str(storage.get("repo") or "").strip()
    if "/" not in repo_full:
        raise RequirementBundleError("bundle.yaml field 'storage.repo' must be in 'owner/repo' format")
    owner, repo = repo_full.split("/", 1)
    if not owner or not repo:
        raise RequirementBundleError("bundle.yaml field 'storage.repo' must be in 'owner/repo' format")

    links = manifest.get("links")
    if not isinstance(links, dict):
        raise RequirementBundleError("bundle.yaml field 'links' must be an object")
    for key in ("requirements_file", "test_cases_file"):
        if key not in links:
            raise RequirementBundleError(f"bundle.yaml missing required field: links.{key}")
        value = links.get(key)
        if not isinstance(value, str) or not value.strip():
            raise RequirementBundleError(f"bundle.yaml field 'links.{key}' must be a non-empty string")


def validate_requirements_doc(requirements_doc: Dict[str, Any]) -> None:
    if not isinstance(requirements_doc, dict):
        raise RequirementBundleError("requirements.yaml must be an object")
    required_top_level = (
        "bundle_id",
        "sources",
        "summary",
        "functional_requirements",
        "business_rules",
        "acceptance_criteria",
        "edge_cases",
        "quality_flags",
    )
    for key in required_top_level:
        if key not in requirements_doc:
            raise RequirementBundleError(f"requirements.yaml missing required field: {key}")
