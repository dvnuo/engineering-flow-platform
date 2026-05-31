import base64
import pytest

from tests._lightweight_requirement_bundle_assets_loader import (
    load_requirement_bundle_assets_lightweight,
)


def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("utf-8")


def _valid_manifest_yaml() -> str:
    return (
        "bundle_id: rb-1\n"
        "title: Maker Checker\n"
        "status: draft\n"
        "scope:\n"
        "  domain: payments\n"
        "  summary: maker checker\n"
        "storage:\n"
        "  repo: acme/assets\n"
        "  path: requirement-bundles/payments/maker\n"
        "  base_branch: main\n"
        "  working_branch: bundle/1\n"
        "links:\n"
        "  requirements_file: requirements.yaml\n"
        "  test_cases_file: test-cases.yaml\n"
    )


def test_parse_bundle_ref_and_github_doc_ref_lightweight():
    module, cleanup = load_requirement_bundle_assets_lightweight()
    try:
        ref = module.parse_bundle_ref({"repo": "acme/assets", "path": "bundles/a", "branch": "main"})
        assert ref.repo == "assets"
        assert ref.owner == "acme"

        doc_ref = module.parse_github_doc_ref(
            "https://github.com/acme/repo/blob/main/docs/spec.md",
            ref,
        )
        assert doc_ref.owner == "acme"
        assert doc_ref.repo == "repo"
        assert doc_ref.path == "docs/spec.md"
    finally:
        cleanup()


def test_resolve_bundle_link_helpers_and_template_id_lightweight():
    module, cleanup = load_requirement_bundle_assets_lightweight()
    try:
        manifest = {
            "storage": {
                "repo": "acme/assets",
                "path": "requirement-bundles/payments/maker",
                "working_branch": "bundle/1",
            },
            "links": {
                "requirements_file": "docs/requirements.yaml",
                "test_cases_file": "docs/test-cases.yaml",
            },
            "template_id": "requirement.v1",
        }
        requirements_file, test_cases_file = module.resolve_bundle_links(manifest)
        assert requirements_file == "docs/requirements.yaml"
        assert test_cases_file == "docs/test-cases.yaml"

        target = module.resolve_target_bundle_ref(
            {"repo": "acme/assets", "path": "bundles/a", "branch": "main"},
            manifest,
        )
        assert target.branch == "bundle/1"

        assert module.resolve_bundle_template_id(manifest) == "requirement.v1"
    finally:
        cleanup()


def test_validate_bundle_manifest_lightweight():
    module, cleanup = load_requirement_bundle_assets_lightweight()
    try:
        manifest = {
            "bundle_id": "rb-1",
            "title": "T",
            "status": "draft",
            "scope": {"domain": "d", "summary": "s"},
            "storage": {"repo": "acme/assets", "path": "bundles/a", "base_branch": "main", "working_branch": "bundle/1"},
            "links": {"requirements_file": "requirements.yaml", "test_cases_file": "test-cases.yaml"},
        }
        module.validate_bundle_manifest(manifest)
    finally:
        cleanup()


@pytest.mark.asyncio
async def test_load_manifest_read_doc_write_and_context_lightweight(monkeypatch):
    module, cleanup = load_requirement_bundle_assets_lightweight()
    try:
        async def _fake_get_file(owner, repo, path, ref=""):
            if path.endswith("bundle.yaml"):
                return {"content": _b64(_valid_manifest_yaml())}
            if path == "docs/spec.md":
                return {"content": _b64("# Spec\nHello")}
            raise AssertionError(path)

        writes = []

        async def _fake_put_file(owner, repo, path, content, message, sha=None, branch=""):
            writes.append((path, branch, content))
            return {"commit": {"sha": "sha-1"}}

        monkeypatch.setattr(module.github_cli, "get_file", _fake_get_file)
        monkeypatch.setattr(module.github_cli, "create_or_update_file", _fake_put_file)

        ref, manifest = await module.load_bundle_manifest({"repo": "acme/assets", "path": "requirement-bundles/payments/maker", "branch": "bundle/1"})
        assert manifest["bundle_id"] == "rb-1"
        assert ref.repo_full_name == "acme/assets"

        default_ref = module.parse_bundle_ref({"repo": "acme/assets", "path": "requirement-bundles/payments/maker", "branch": "bundle/1"})
        _, text = await module.read_github_doc_text("docs/spec.md", default_ref)
        assert "Spec" in text

        await module.write_requirements_doc_for_ref(default_ref, {"bundle_id": "rb-1"}, requirements_file="requirements.yaml")
        assert writes and writes[0][0].endswith("requirements.yaml")

        context = module.build_test_design_context(
            {"bundle_id": "rb-1", "title": "T", "scope": {"domain": "d"}},
            {"summary": {}, "functional_requirements": ["A"]},
        )
        assert context["bundle_id"] == "rb-1"
    finally:
        cleanup()
