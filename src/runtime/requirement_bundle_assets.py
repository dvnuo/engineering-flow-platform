from __future__ import annotations

import base64
import io
from dataclasses import dataclass
from typing import Any, Dict, Tuple

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
    file_data = await github_channel.get_file(ref.owner, ref.repo, file_path, ref.branch)
    content = file_data.get("content")
    if not isinstance(content, str) or not content.strip():
        raise RequirementBundleError(f"File not found or empty: {file_path}")
    try:
        return base64.b64decode(content).decode("utf-8")
    except Exception as exc:  # pragma: no cover - defensive
        raise RequirementBundleError(f"Failed to decode file content: {file_path}") from exc


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
    return ref, manifest


async def load_requirements_doc(bundle_ref: Dict[str, Any]) -> Tuple[BundleRef, Dict[str, Any]]:
    ref = parse_bundle_ref(bundle_ref)
    requirements = await read_bundle_yaml(ref, "requirements.yaml")
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
    return await write_bundle_yaml(
        ref,
        "requirements.yaml",
        payload,
        f"chore(requirement-bundle): update requirements.yaml for {ref.path}",
    )


async def write_test_cases_doc(bundle_ref: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
    ref = parse_bundle_ref(bundle_ref)
    return await write_bundle_yaml(
        ref,
        "test-cases.yaml",
        payload,
        f"chore(requirement-bundle): update test-cases.yaml for {ref.path}",
    )


def build_test_design_context(bundle_manifest: Dict[str, Any], requirements_doc: Dict[str, Any]) -> Dict[str, Any]:
    return {
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
