from __future__ import annotations

import importlib.util
import base64
import sys
import types
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


def load_requirement_bundle_assets_lightweight():
    src_pkg = types.ModuleType("src")
    src_pkg.__path__ = []

    runtime_pkg = types.ModuleType("src.runtime")
    runtime_pkg.__path__ = []

    external_cli_pkg = types.ModuleType("src.external_cli")
    external_cli_pkg.__path__ = []

    github_cli_mod = types.ModuleType("src.external_cli.github")
    github_cli = types.SimpleNamespace(
        base_url="https://api.github.com",
        get_file=None,
        create_or_update_file=None,
    )

    @dataclass(frozen=True)
    class GitHubDocRef:
        owner: str
        repo: str
        branch: str
        path: str

    def parse_github_doc_ref(raw, default_ref):
        normalized = str(raw or "").strip()
        if not normalized:
            raise ValueError("github_doc_ref is required")
        if normalized.startswith(("http://", "https://")):
            parsed = urlparse(normalized)
            parts = [part for part in parsed.path.split("/") if part]
            if len(parts) < 5 or parts[2] != "blob":
                raise ValueError("GitHub doc URL must be in /owner/repo/blob/<branch>/<path> format")
            return GitHubDocRef(
                owner=parts[0],
                repo=parts[1],
                branch=parts[3],
                path="/".join(parts[4:]).strip("/"),
            )
        return GitHubDocRef(
            owner=getattr(default_ref, "owner", None) or default_ref["owner"],
            repo=getattr(default_ref, "repo", None) or default_ref["repo"],
            branch=getattr(default_ref, "branch", None) or default_ref["branch"],
            path=normalized.strip("/"),
        )

    async def prepare_github_file_source(raw, default_ref, session_id=None):
        doc_ref = parse_github_doc_ref(raw, default_ref)
        file_data = await github_cli_mod.get_file(doc_ref.owner, doc_ref.repo, doc_ref.path, doc_ref.branch)
        encoded = file_data.get("content")
        if not isinstance(encoded, str) or not encoded.strip():
            raise ValueError(f"File not found or empty: {doc_ref.path}")
        content_markdown = base64.b64decode(encoded).decode("utf-8")
        bundle = {
            "content_markdown": content_markdown,
            "artifact_refs": [],
            "context_ref": None,
            "digest_ref": None,
        }
        return {"doc_ref": doc_ref, "bundle": bundle}

    async def _get_file(*args, **kwargs):
        return await github_cli.get_file(*args, **kwargs)

    async def _create_or_update_file(*args, **kwargs):
        return await github_cli.create_or_update_file(*args, **kwargs)

    github_cli_mod.GitHubDocRef = GitHubDocRef
    github_cli_mod.parse_github_doc_ref = parse_github_doc_ref
    github_cli_mod.prepare_github_file_source = prepare_github_file_source
    github_cli_mod.get_file = _get_file
    github_cli_mod.create_or_update_file = _create_or_update_file

    bundle_template_registry_mod = types.ModuleType("src.runtime.bundle_template_registry")

    def require_bundle_template(template_id):
        normalized = str(template_id or "").strip().lower()
        if normalized in {"requirement.v1", "research.v1", "development.v1", "operations.v1"}:
            return types.SimpleNamespace(template_id=normalized)
        raise ValueError(f"Unsupported bundle template_id: {template_id}")

    def resolve_bundle_template_id_from_manifest(manifest):
        normalized = str((manifest or {}).get("template_id") or "").strip().lower()
        if normalized:
            return require_bundle_template(normalized).template_id
        links = (manifest or {}).get("links")
        if isinstance(links, dict) and links:
            return "requirement.v1"
        raise ValueError("bundle.yaml requires 'template_id' or legacy 'links'")

    bundle_template_registry_mod.require_bundle_template = require_bundle_template
    bundle_template_registry_mod.resolve_bundle_template_id_from_manifest = resolve_bundle_template_id_from_manifest

    redaction_mod = types.ModuleType("src.utils.redaction")
    redaction_mod.safe_preview = lambda value, max_length=120: str(value)[:max_length]
    redaction_mod.sanitize_exception_message = lambda exc: str(exc)

    ruamel_mod = types.ModuleType("ruamel")
    ruamel_mod.__path__ = []
    ruamel_yaml_mod = types.ModuleType("ruamel.yaml")
    try:
        import yaml as _pyyaml
    except Exception:  # pragma: no cover - fallback for extremely minimal env
        _pyyaml = None

    class YAML:  # noqa: N801 - mirror ruamel API
        def __init__(self):
            self.default_flow_style = False

        def _load_minimal_yaml(self, text):
            root = {}
            stack = [(-1, root)]
            for raw_line in str(text or "").splitlines():
                if not raw_line.strip() or raw_line.lstrip().startswith("#"):
                    continue
                indent = len(raw_line) - len(raw_line.lstrip(" "))
                stripped = raw_line.strip()
                if ":" not in stripped:
                    raise RuntimeError(f"Unsupported YAML line: {raw_line}")
                key, _, value = stripped.partition(":")
                key = key.strip()
                value = value.strip()
                while stack and indent <= stack[-1][0]:
                    stack.pop()
                parent = stack[-1][1]
                if value == "":
                    node = {}
                    parent[key] = node
                    stack.append((indent, node))
                else:
                    parent[key] = value
            return root

        def _dump_minimal_yaml(self, payload, stream, indent=0):
            for key, value in (payload or {}).items():
                if isinstance(value, dict):
                    stream.write(f"{' ' * indent}{key}:\n")
                    self._dump_minimal_yaml(value, stream, indent=indent + 2)
                else:
                    stream.write(f"{' ' * indent}{key}: {value}\n")

        def load(self, text):
            if _pyyaml is not None:
                return _pyyaml.safe_load(text)
            return self._load_minimal_yaml(text)

        def dump(self, payload, stream):
            if _pyyaml is not None:
                return _pyyaml.safe_dump(payload, stream, sort_keys=False)
            self._dump_minimal_yaml(payload, stream)
            return None

    ruamel_yaml_mod.YAML = YAML

    context_blob_mod = types.ModuleType("src.context_blob_store")
    context_blob_mod.read_ref = lambda *args, **kwargs: ""
    context_blob_mod.put_text = lambda **kwargs: "ctx://context/s/k/sha"

    modules = {
        "src": src_pkg,
        "src.runtime": runtime_pkg,
        "src.external_cli": external_cli_pkg,
        "src.external_cli.github": github_cli_mod,
        "src.runtime.bundle_template_registry": bundle_template_registry_mod,
        "src.utils.redaction": redaction_mod,
        "src.context_blob_store": context_blob_mod,
        "ruamel": ruamel_mod,
        "ruamel.yaml": ruamel_yaml_mod,
    }
    prev = {name: sys.modules.get(name) for name in modules}
    sys.modules.update(modules)
    src_pkg.runtime = runtime_pkg
    src_pkg.external_cli = external_cli_pkg
    external_cli_pkg.github = github_cli_mod

    spec = importlib.util.spec_from_file_location(
        "src.runtime.requirement_bundle_assets",
        Path("src/runtime/requirement_bundle_assets.py"),
    )
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules["src.runtime.requirement_bundle_assets"] = module
    spec.loader.exec_module(module)

    def _cleanup():
        for name, old in prev.items():
            if old is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old
        sys.modules.pop("src.runtime.requirement_bundle_assets", None)

    return module, _cleanup
