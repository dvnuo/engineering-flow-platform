import base64
import json

import pytest

from skills.collect_requirements_to_bundle.skill import collect_requirements_to_bundle as collect_requirements_skill
from skills.design_test_cases_from_bundle.skill import design_test_cases_from_bundle as design_test_cases_skill
from src.runtime.requirement_bundle_assets import (
    RequirementBundleError,
    BundleRef,
    build_test_design_context,
    load_bundle_manifest,
    parse_github_doc_ref,
    parse_bundle_ref,
    read_github_doc_text,
    resolve_bundle_links,
    resolve_target_bundle_ref,
    validate_bundle_manifest,
    write_requirements_doc_for_ref,
)


def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("utf-8")


def _valid_manifest_yaml(
    bundle_id: str = "rb-1", requirements_file: str = "requirements.yaml", test_cases_file: str = "test-cases.yaml"
) -> str:
    return (
        f"bundle_id: {bundle_id}\n"
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
        f"  requirements_file: {requirements_file}\n"
        f"  test_cases_file: {test_cases_file}\n"
    )


@pytest.mark.asyncio
async def test_collect_skill_reads_manifest_and_writes_requirements(monkeypatch, caplog):
    writes = []
    observed_paths = []

    async def _fake_get_file(owner, repo, path, ref=""):
        observed_paths.append(path)
        if path.endswith("bundle.yaml"):
            return {"content": _b64(_valid_manifest_yaml("rb-1"))}
        if path == "docs/spec.md":
            return {"content": _b64("# Spec\nHello")}
        raise AssertionError(f"unexpected get_file path: {path}")

    async def _fake_put_file(owner, repo, path, content, message, sha=None, branch=""):
        writes.append({"owner": owner, "repo": repo, "path": path, "content": content, "branch": branch})
        return {"commit": {"sha": "sha-req"}}

    async def _fake_chat(self, **_kwargs):
        return {
            "content": json.dumps(
                {
                    "summary": {"text": "ok"},
                    "functional_requirements": ["FR-1"],
                    "business_rules": ["BR-1"],
                    "acceptance_criteria": ["AC-1"],
                    "edge_cases": ["EC-1"],
                    "quality_flags": {"ambiguities": [], "conflicts": [], "missing_information": []},
                }
            )
        }

    async def _fake_jira_issue(issue_key, **kwargs):
        return f"jira:{issue_key}"

    async def _fake_confluence_page(page_id, **kwargs):
        return f"confluence:{page_id}"

    monkeypatch.setattr("skills.collect_requirements_to_bundle.skill.github_channel.is_configured", lambda: True)
    monkeypatch.setattr("skills.collect_requirements_to_bundle.skill.github_channel.get_file", _fake_get_file)
    monkeypatch.setattr("src.runtime.requirement_bundle_assets.github_channel.get_file", _fake_get_file)
    monkeypatch.setattr("src.runtime.requirement_bundle_assets.github_channel.create_or_update_file", _fake_put_file)
    monkeypatch.setattr("src.agents.llm.LLMClient.chat", _fake_chat)
    monkeypatch.setattr("skills.collect_requirements_to_bundle.skill.jira_get_issue", _fake_jira_issue)
    monkeypatch.setattr("skills.collect_requirements_to_bundle.skill.confluence_get_page", _fake_confluence_page)

    with caplog.at_level("INFO"):
        result = await collect_requirements_skill.execute(
            bundle_ref={"repo": "acme/assets", "path": "requirement-bundles/payments/maker", "branch": "bundle/1"},
            sources={"jira": ["PAY-101"], "confluence": ["9876"], "github_docs": ["docs/spec.md"], "figma": ["fig-1"]},
        )

    assert result.success is True
    assert writes and writes[0]["branch"] == "bundle/1"
    assert writes[0]["path"].endswith("requirements.yaml")
    assert "figma sources are ignored in MVP" in result.data.get("warnings", [])
    assert "docs/spec.md" in observed_paths
    assert "requirement-bundles/payments/maker/docs/spec.md" not in observed_paths
    assert "Collect requirements skill start" in caplog.text
    assert "Collect requirements skill finish" in caplog.text


@pytest.mark.asyncio
async def test_load_bundle_manifest_failure_logs_reason(monkeypatch, caplog):
    async def _fake_get_file(owner, repo, path, ref=""):
        if path.endswith("bundle.yaml"):
            return {"content": _b64("bundle_id: rb-err\ntitle: bad\nstatus: draft\n")}
        raise AssertionError(path)

    monkeypatch.setattr("src.runtime.requirement_bundle_assets.github_channel.get_file", _fake_get_file)

    with caplog.at_level("WARNING"):
        with pytest.raises(RequirementBundleError):
            await load_bundle_manifest(
                {"repo": "acme/assets", "path": "requirement-bundles/payments/maker", "branch": "bundle/1"}
            )

    assert "Bundle asset action failed | action=load_bundle_manifest" in caplog.text
    assert "bundle.yaml missing required field" in caplog.text


@pytest.mark.asyncio
async def test_read_github_doc_text_failure_logs_reason(monkeypatch, caplog):
    async def _fake_get_file(owner, repo, path, ref=""):
        return {"content": ""}

    monkeypatch.setattr("src.runtime.requirement_bundle_assets.github_channel.get_file", _fake_get_file)
    default_ref = BundleRef(owner="acme", repo="assets", path="bundles/a", branch="main")

    with caplog.at_level("WARNING"):
        with pytest.raises(RequirementBundleError):
            await read_github_doc_text("docs/spec.md", default_ref)

    assert "Bundle asset action failed | action=read_github_doc_text" in caplog.text
    assert "File not found or empty" in caplog.text


@pytest.mark.asyncio
async def test_write_requirements_doc_failure_logs_ref_and_file(monkeypatch, caplog):
    async def _fake_put_file(owner, repo, path, content, message, sha=None, branch=""):
        raise RuntimeError("write failed")

    monkeypatch.setattr("src.runtime.requirement_bundle_assets.github_channel.create_or_update_file", _fake_put_file)
    ref = BundleRef(owner="acme", repo="assets", path="requirement-bundles/payments/maker", branch="bundle/1")

    with caplog.at_level("WARNING"):
        with pytest.raises(RuntimeError):
            await write_requirements_doc_for_ref(
                ref,
                {"bundle_id": "rb-1", "sources": {}, "summary": {}, "functional_requirements": [], "business_rules": [], "acceptance_criteria": [], "edge_cases": [], "quality_flags": {"ambiguities": [], "conflicts": [], "missing_information": []}},
                requirements_file="docs/requirements.yaml",
            )

    assert "Bundle asset action failed | action=write_bundle_yaml" in caplog.text
    assert "Bundle asset action failed | action=write_requirements_doc_for_ref" in caplog.text
    assert "repo=acme/assets" in caplog.text
    assert "relative_file=docs/requirements.yaml" in caplog.text
    assert "write failed" in caplog.text


@pytest.mark.asyncio
async def test_collect_skill_failure_logs_sanitized_reason(monkeypatch, caplog):
    async def _raise_manifest(*_args, **_kwargs):
        raise RequirementBundleError("manifest invalid")

    monkeypatch.setattr("skills.collect_requirements_to_bundle.skill.github_channel.is_configured", lambda: True)
    monkeypatch.setattr("skills.collect_requirements_to_bundle.skill.load_bundle_manifest", _raise_manifest)

    with caplog.at_level("WARNING"):
        result = await collect_requirements_skill.execute(
            bundle_ref={"repo": "acme/assets", "path": "requirement-bundles/payments/maker", "branch": "bundle/1"},
            sources={"github_docs": ["docs/spec.md"]},
        )

    assert result.success is False
    assert "action=collect_requirements_to_bundle" in caplog.text
    assert "manifest invalid" in caplog.text


@pytest.mark.asyncio
async def test_collect_skill_supports_jira_browse_url(monkeypatch):
    calls = {"by_url": []}

    async def _fake_get_file(owner, repo, path, ref=""):
        if path.endswith("bundle.yaml"):
            return {"content": _b64(_valid_manifest_yaml("rb-jira-url"))}
        if path == "docs/spec.md":
            return {"content": _b64("# Spec")}
        raise AssertionError(path)

    async def _fake_put_file(owner, repo, path, content, message, sha=None, branch=""):
        return {"commit": {"sha": "sha"}}

    async def _fake_jira_get_issue(_issue_key, **kwargs):
        return "jira-key"

    async def _fake_jira_get_issue_by_url(url, **kwargs):
        calls["by_url"].append(url)
        return "jira-url"

    async def _fake_chat(self, **kwargs):
        return {"content": "{\"summary\":{},\"functional_requirements\":[\"a\"],\"business_rules\":[],\"acceptance_criteria\":[],\"edge_cases\":[],\"quality_flags\":{\"ambiguities\":[],\"conflicts\":[],\"missing_information\":[]}}"}

    monkeypatch.setattr("skills.collect_requirements_to_bundle.skill.github_channel.is_configured", lambda: True)
    monkeypatch.setattr("src.runtime.requirement_bundle_assets.github_channel.get_file", _fake_get_file)
    monkeypatch.setattr("src.runtime.requirement_bundle_assets.github_channel.create_or_update_file", _fake_put_file)
    monkeypatch.setattr("skills.collect_requirements_to_bundle.skill.jira_get_issue", _fake_jira_get_issue)
    monkeypatch.setattr("skills.collect_requirements_to_bundle.skill.jira_get_issue_by_url", _fake_jira_get_issue_by_url)
    monkeypatch.setattr("skills.collect_requirements_to_bundle.skill.confluence_get_page", _fake_jira_get_issue)
    monkeypatch.setattr("skills.collect_requirements_to_bundle.skill.confluence_get_page_by_url", _fake_jira_get_issue_by_url)
    monkeypatch.setattr("src.agents.llm.LLMClient.chat", _fake_chat)

    result = await collect_requirements_skill.execute(
        bundle_ref={"repo": "acme/assets", "path": "requirement-bundles/payments/maker", "branch": "bundle/1"},
        sources={"jira": ["https://jira.example.com/browse/PAY-101"], "github_docs": ["docs/spec.md"]},
    )
    assert result.success is True
    assert calls["by_url"] == ["https://jira.example.com/browse/PAY-101"]


@pytest.mark.asyncio
async def test_collect_skill_supports_confluence_page_url(monkeypatch):
    calls = {"by_url": []}

    async def _fake_get_file(owner, repo, path, ref=""):
        if path.endswith("bundle.yaml"):
            return {"content": _b64(_valid_manifest_yaml("rb-cf-url"))}
        if path == "docs/spec.md":
            return {"content": _b64("# Spec")}
        raise AssertionError(path)

    async def _fake_put_file(owner, repo, path, content, message, sha=None, branch=""):
        return {"commit": {"sha": "sha"}}

    async def _fake_confluence_get_page(_page_id, **kwargs):
        return "confluence-id"

    async def _fake_confluence_get_page_by_url(url, **kwargs):
        calls["by_url"].append(url)
        return "confluence-url"

    async def _fake_chat(self, **kwargs):
        return {"content": "{\"summary\":{},\"functional_requirements\":[\"a\"],\"business_rules\":[],\"acceptance_criteria\":[],\"edge_cases\":[],\"quality_flags\":{\"ambiguities\":[],\"conflicts\":[],\"missing_information\":[]}}"}

    monkeypatch.setattr("skills.collect_requirements_to_bundle.skill.github_channel.is_configured", lambda: True)
    monkeypatch.setattr("src.runtime.requirement_bundle_assets.github_channel.get_file", _fake_get_file)
    monkeypatch.setattr("src.runtime.requirement_bundle_assets.github_channel.create_or_update_file", _fake_put_file)
    monkeypatch.setattr("skills.collect_requirements_to_bundle.skill.confluence_get_page", _fake_confluence_get_page)
    monkeypatch.setattr("skills.collect_requirements_to_bundle.skill.confluence_get_page_by_url", _fake_confluence_get_page_by_url)
    monkeypatch.setattr("skills.collect_requirements_to_bundle.skill.jira_get_issue", _fake_confluence_get_page)
    monkeypatch.setattr("skills.collect_requirements_to_bundle.skill.jira_get_issue_by_url", _fake_confluence_get_page_by_url)
    monkeypatch.setattr("src.agents.llm.LLMClient.chat", _fake_chat)

    result = await collect_requirements_skill.execute(
        bundle_ref={"repo": "acme/assets", "path": "requirement-bundles/payments/maker", "branch": "bundle/1"},
        sources={"confluence": ["https://wiki.example.com/pages/viewpage.action?pageId=987654"], "github_docs": ["docs/spec.md"]},
    )
    assert result.success is True
    assert calls["by_url"] == ["https://wiki.example.com/pages/viewpage.action?pageId=987654"]


@pytest.mark.asyncio
async def test_collect_skill_supports_github_blob_url_cross_repo(monkeypatch):
    observed = []

    async def _fake_get_file(owner, repo, path, ref=""):
        observed.append((owner, repo, path, ref))
        if path.endswith("bundle.yaml"):
            return {"content": _b64(_valid_manifest_yaml("rb-gh-url"))}
        if owner == "org" and repo == "repo" and path == "docs/spec.md" and ref == "main":
            return {"content": _b64("# External Spec")}
        raise AssertionError((owner, repo, path, ref))

    async def _fake_put_file(owner, repo, path, content, message, sha=None, branch=""):
        return {"commit": {"sha": "sha"}}

    async def _fake_chat(self, **kwargs):
        return {"content": "{\"summary\":{},\"functional_requirements\":[\"a\"],\"business_rules\":[],\"acceptance_criteria\":[],\"edge_cases\":[],\"quality_flags\":{\"ambiguities\":[],\"conflicts\":[],\"missing_information\":[]}}"}

    async def _fake_text(*args, **kwargs):
        return "ok"

    monkeypatch.setattr("skills.collect_requirements_to_bundle.skill.github_channel.is_configured", lambda: True)
    monkeypatch.setattr("src.runtime.requirement_bundle_assets.github_channel.get_file", _fake_get_file)
    monkeypatch.setattr("src.runtime.requirement_bundle_assets.github_channel.create_or_update_file", _fake_put_file)
    monkeypatch.setattr("skills.collect_requirements_to_bundle.skill.jira_get_issue", _fake_text)
    monkeypatch.setattr("skills.collect_requirements_to_bundle.skill.jira_get_issue_by_url", _fake_text)
    monkeypatch.setattr("skills.collect_requirements_to_bundle.skill.confluence_get_page", _fake_text)
    monkeypatch.setattr("skills.collect_requirements_to_bundle.skill.confluence_get_page_by_url", _fake_text)
    monkeypatch.setattr("src.agents.llm.LLMClient.chat", _fake_chat)

    result = await collect_requirements_skill.execute(
        bundle_ref={"repo": "acme/assets", "path": "requirement-bundles/payments/maker", "branch": "bundle/1"},
        sources={"github_docs": ["https://github.com/org/repo/blob/main/docs/spec.md"]},
    )
    assert result.success is True
    assert ("org", "repo", "docs/spec.md", "main") in observed


@pytest.mark.asyncio
async def test_collect_skill_supports_enterprise_github_blob_url(monkeypatch):
    observed = []

    async def _fake_get_file(owner, repo, path, ref=""):
        observed.append((owner, repo, path, ref))
        if path.endswith("bundle.yaml"):
            return {"content": _b64(_valid_manifest_yaml("rb-gh-enterprise"))}
        if owner == "org" and repo == "repo" and path == "docs/spec.md" and ref == "main":
            return {"content": _b64("# Enterprise Spec")}
        raise AssertionError((owner, repo, path, ref))

    async def _fake_put_file(owner, repo, path, content, message, sha=None, branch=""):
        return {"commit": {"sha": "sha"}}

    async def _fake_chat(self, **kwargs):
        return {"content": "{\"summary\":{},\"functional_requirements\":[\"a\"],\"business_rules\":[],\"acceptance_criteria\":[],\"edge_cases\":[],\"quality_flags\":{\"ambiguities\":[],\"conflicts\":[],\"missing_information\":[]}}"}

    async def _fake_text(*args, **kwargs):
        return "ok"

    monkeypatch.setattr("skills.collect_requirements_to_bundle.skill.github_channel.is_configured", lambda: True)
    monkeypatch.setattr("src.runtime.requirement_bundle_assets.github_channel.get_file", _fake_get_file)
    monkeypatch.setattr("src.runtime.requirement_bundle_assets.github_channel.create_or_update_file", _fake_put_file)
    monkeypatch.setattr("skills.collect_requirements_to_bundle.skill.jira_get_issue", _fake_text)
    monkeypatch.setattr("skills.collect_requirements_to_bundle.skill.jira_get_issue_by_url", _fake_text)
    monkeypatch.setattr("skills.collect_requirements_to_bundle.skill.confluence_get_page", _fake_text)
    monkeypatch.setattr("skills.collect_requirements_to_bundle.skill.confluence_get_page_by_url", _fake_text)
    monkeypatch.setattr("src.agents.llm.LLMClient.chat", _fake_chat)
    monkeypatch.setattr("src.runtime.requirement_bundle_assets.github_channel.hostname", "github.company.com")

    result = await collect_requirements_skill.execute(
        bundle_ref={"repo": "acme/assets", "path": "requirement-bundles/payments/maker", "branch": "bundle/1"},
        sources={"github_docs": ["https://github.company.com/org/repo/blob/main/docs/spec.md"]},
    )
    assert result.success is True
    assert ("org", "repo", "docs/spec.md", "main") in observed


@pytest.mark.asyncio
async def test_collect_prompt_uses_scope_summary_from_manifest(monkeypatch):
    seen_prompt = {"payload": None}

    async def _fake_get_file(owner, repo, path, ref=""):
        if path.endswith("bundle.yaml"):
            return {"content": _b64(_valid_manifest_yaml("rb-summary"))}
        if path == "docs/spec.md":
            return {"content": _b64("# Spec")}
        raise AssertionError((owner, repo, path, ref))

    async def _fake_put_file(owner, repo, path, content, message, sha=None, branch=""):
        return {"commit": {"sha": "sha"}}

    async def _fake_chat(self, **kwargs):
        seen_prompt["payload"] = json.loads(kwargs["messages"][0]["content"])
        return {"content": "{\"summary\":{},\"functional_requirements\":[\"a\"],\"business_rules\":[],\"acceptance_criteria\":[],\"edge_cases\":[],\"quality_flags\":{\"ambiguities\":[],\"conflicts\":[],\"missing_information\":[]}}"}

    monkeypatch.setattr("skills.collect_requirements_to_bundle.skill.github_channel.is_configured", lambda: True)
    monkeypatch.setattr("src.runtime.requirement_bundle_assets.github_channel.get_file", _fake_get_file)
    monkeypatch.setattr("src.runtime.requirement_bundle_assets.github_channel.create_or_update_file", _fake_put_file)
    monkeypatch.setattr("src.agents.llm.LLMClient.chat", _fake_chat)

    result = await collect_requirements_skill.execute(
        bundle_ref={"repo": "acme/assets", "path": "requirement-bundles/payments/maker", "branch": "bundle/1"},
        sources={"github_docs": ["docs/spec.md"]},
    )

    assert result.success is True
    assert seen_prompt["payload"] is not None
    assert seen_prompt["payload"]["bundle"]["summary"] == "maker checker"


@pytest.mark.asyncio
async def test_collect_skill_writes_custom_requirements_file_from_manifest_links(monkeypatch):
    writes = []

    async def _fake_get_file(owner, repo, path, ref=""):
        if path.endswith("bundle.yaml"):
            return {
                "content": _b64(
                    _valid_manifest_yaml(
                        "rb-custom-links-collect", requirements_file="docs/reqs.yaml", test_cases_file="outputs/tc.yaml"
                    )
                )
            }
        if path == "docs/spec.md":
            return {"content": _b64("# Spec")}
        raise AssertionError((owner, repo, path, ref))

    async def _fake_put_file(owner, repo, path, content, message, sha=None, branch=""):
        writes.append({"path": path, "branch": branch})
        return {"commit": {"sha": "sha-custom-collect"}}

    async def _fake_chat(self, **kwargs):
        return {"content": "{\"summary\":{},\"functional_requirements\":[\"a\"],\"business_rules\":[],\"acceptance_criteria\":[],\"edge_cases\":[],\"quality_flags\":{\"ambiguities\":[],\"conflicts\":[],\"missing_information\":[]}}"}

    monkeypatch.setattr("skills.collect_requirements_to_bundle.skill.github_channel.is_configured", lambda: True)
    monkeypatch.setattr("src.runtime.requirement_bundle_assets.github_channel.get_file", _fake_get_file)
    monkeypatch.setattr("src.runtime.requirement_bundle_assets.github_channel.create_or_update_file", _fake_put_file)
    monkeypatch.setattr("src.agents.llm.LLMClient.chat", _fake_chat)

    result = await collect_requirements_skill.execute(
        bundle_ref={"repo": "acme/assets", "path": "requirement-bundles/payments/maker", "branch": "bundle/1"},
        sources={"github_docs": ["docs/spec.md"]},
    )

    assert result.success is True
    assert writes and writes[0]["path"] == "requirement-bundles/payments/maker/docs/reqs.yaml"
    assert result.data["updated_files"] == ["requirement-bundles/payments/maker/docs/reqs.yaml"]


@pytest.mark.asyncio
async def test_design_skill_reads_requirements_and_writes_test_cases(monkeypatch):
    writes = []

    async def _fake_get_file(owner, repo, path, ref=""):
        if path.endswith("bundle.yaml"):
            return {"content": _b64(_valid_manifest_yaml("rb-2"))}
        if path.endswith("requirements.yaml"):
            return {
                "content": _b64(
                    "bundle_id: rb-2\nsources: {}\nsummary: {text: ok}\nfunctional_requirements: [FR-1]\nbusiness_rules: [BR-1]\nacceptance_criteria: [AC-1]\nedge_cases: [EC-1]\nquality_flags:\n  ambiguities: []\n  conflicts: []\n  missing_information: []\n"
                )
            }
        raise AssertionError(path)

    async def _fake_put_file(owner, repo, path, content, message, sha=None, branch=""):
        writes.append({"path": path, "content": content, "branch": branch})
        return {"commit": {"sha": "sha-tc"}}

    async def _fake_chat(self, **_kwargs):
        return {
            "content": "```json\n{\"test_cases\":[{\"case_id\":\"TC-1\",\"title\":\"happy\",\"category\":\"functional\",\"priority\":\"P1\",\"preconditions\":[],\"steps\":[],\"expected_results\":[],\"traceability\":[\"FR-1\"]}]}\n```"
        }

    monkeypatch.setattr("src.runtime.requirement_bundle_assets.github_channel.get_file", _fake_get_file)
    monkeypatch.setattr("src.runtime.requirement_bundle_assets.github_channel.create_or_update_file", _fake_put_file)
    monkeypatch.setattr("src.agents.llm.LLMClient.chat", _fake_chat)

    result = await design_test_cases_skill.execute(
        bundle_ref={"repo": "acme/assets", "path": "requirement-bundles/payments/maker", "branch": "bundle/2"}
    )

    assert result.success is True
    assert writes and writes[0]["path"].endswith("test-cases.yaml")
    assert writes[0]["branch"] == "bundle/1"


@pytest.mark.asyncio
async def test_design_skill_reads_writes_custom_linked_files(monkeypatch):
    writes = []
    reads = []

    async def _fake_get_file(owner, repo, path, ref=""):
        reads.append((path, ref))
        if path.endswith("bundle.yaml"):
            return {
                "content": _b64(
                    _valid_manifest_yaml(
                        "rb-custom-links-design", requirements_file="docs/reqs.yaml", test_cases_file="outputs/tc.yaml"
                    )
                )
            }
        if path.endswith("docs/reqs.yaml"):
            return {
                "content": _b64(
                    "bundle_id: rb-custom-links-design\nsources: {}\nsummary: {text: ok}\nfunctional_requirements: [FR-1]\nbusiness_rules: []\nacceptance_criteria: []\nedge_cases: []\nquality_flags:\n  ambiguities: []\n  conflicts: []\n  missing_information: []\n"
                )
            }
        raise AssertionError((owner, repo, path, ref))

    async def _fake_put_file(owner, repo, path, content, message, sha=None, branch=""):
        writes.append({"path": path, "branch": branch})
        return {"commit": {"sha": "sha-custom-design"}}

    async def _fake_chat(self, **_kwargs):
        return {
            "content": "{\"test_cases\":[{\"case_id\":\"TC-1\",\"title\":\"happy\",\"category\":\"functional\",\"priority\":\"P1\",\"preconditions\":[],\"steps\":[],\"expected_results\":[],\"traceability\":[\"FR-1\"]}]}"
        }

    monkeypatch.setattr("src.runtime.requirement_bundle_assets.github_channel.get_file", _fake_get_file)
    monkeypatch.setattr("src.runtime.requirement_bundle_assets.github_channel.create_or_update_file", _fake_put_file)
    monkeypatch.setattr("src.agents.llm.LLMClient.chat", _fake_chat)

    result = await design_test_cases_skill.execute(
        bundle_ref={"repo": "acme/assets", "path": "requirement-bundles/payments/maker", "branch": "bundle/1"}
    )

    assert result.success is True
    assert ("requirement-bundles/payments/maker/docs/reqs.yaml", "bundle/1") in reads
    assert writes and writes[0]["path"] == "requirement-bundles/payments/maker/outputs/tc.yaml"
    assert result.data["updated_files"] == ["requirement-bundles/payments/maker/outputs/tc.yaml"]


@pytest.mark.asyncio
async def test_collect_skill_respects_manifest_working_branch(monkeypatch):
    writes = []

    async def _fake_get_file(owner, repo, path, ref=""):
        if path.endswith("bundle.yaml"):
            return {"content": _b64(_valid_manifest_yaml("rb-working-collect").replace("working_branch: bundle/1", "working_branch: bundle/checkout/abcd1234"))}
        if path == "docs/spec.md" and ref == "bundle/checkout/abcd1234":
            return {"content": _b64("# Canonical Spec")}
        raise AssertionError((owner, repo, path, ref))

    async def _fake_put_file(owner, repo, path, content, message, sha=None, branch=""):
        writes.append({"owner": owner, "repo": repo, "path": path, "content": content, "branch": branch})
        return {"commit": {"sha": "sha-canonical-req"}}

    async def _fake_chat(self, **_kwargs):
        return {
            "content": "{\"summary\":{},\"functional_requirements\":[\"FR-1\"],\"business_rules\":[],\"acceptance_criteria\":[],\"edge_cases\":[],\"quality_flags\":{\"ambiguities\":[],\"conflicts\":[],\"missing_information\":[]}}"
        }

    async def _fake_text(*args, **kwargs):
        return "ok"

    monkeypatch.setattr("skills.collect_requirements_to_bundle.skill.github_channel.is_configured", lambda: True)
    monkeypatch.setattr("src.runtime.requirement_bundle_assets.github_channel.get_file", _fake_get_file)
    monkeypatch.setattr("src.runtime.requirement_bundle_assets.github_channel.create_or_update_file", _fake_put_file)
    monkeypatch.setattr("skills.collect_requirements_to_bundle.skill.jira_get_issue", _fake_text)
    monkeypatch.setattr("skills.collect_requirements_to_bundle.skill.jira_get_issue_by_url", _fake_text)
    monkeypatch.setattr("skills.collect_requirements_to_bundle.skill.confluence_get_page", _fake_text)
    monkeypatch.setattr("skills.collect_requirements_to_bundle.skill.confluence_get_page_by_url", _fake_text)
    monkeypatch.setattr("src.agents.llm.LLMClient.chat", _fake_chat)

    result = await collect_requirements_skill.execute(
        bundle_ref={"repo": "acme/assets", "path": "requirement-bundles/payments/maker", "branch": "main"},
        sources={"github_docs": ["docs/spec.md"]},
    )

    assert result.success is True
    assert writes and writes[0]["branch"] == "bundle/checkout/abcd1234"
    assert result.data["bundle_ref"]["branch"] == "bundle/checkout/abcd1234"


@pytest.mark.asyncio
async def test_design_skill_reads_writes_via_manifest_working_branch(monkeypatch):
    writes = []
    reads = []

    async def _fake_get_file(owner, repo, path, ref=""):
        reads.append((path, ref))
        if path.endswith("bundle.yaml") and ref == "main":
            yaml = _valid_manifest_yaml("rb-working-design").replace("working_branch: bundle/1", "working_branch: bundle/checkout/abcd1234")
            return {"content": _b64(yaml)}
        if path.endswith("requirements.yaml") and ref == "bundle/checkout/abcd1234":
            return {
                "content": _b64(
                    "bundle_id: rb-working-design\nsources: {}\nsummary: {text: ok}\nfunctional_requirements: [FR-1]\nbusiness_rules: []\nacceptance_criteria: []\nedge_cases: []\nquality_flags:\n  ambiguities: []\n  conflicts: []\n  missing_information: []\n"
                )
            }
        raise AssertionError((owner, repo, path, ref))

    async def _fake_put_file(owner, repo, path, content, message, sha=None, branch=""):
        writes.append({"path": path, "branch": branch})
        return {"commit": {"sha": "sha-canonical-tc"}}

    async def _fake_chat(self, **_kwargs):
        return {
            "content": "{\"test_cases\":[{\"case_id\":\"TC-1\",\"title\":\"happy\",\"category\":\"functional\",\"priority\":\"P1\",\"preconditions\":[],\"steps\":[],\"expected_results\":[],\"traceability\":[\"FR-1\"]}]}"
        }

    monkeypatch.setattr("src.runtime.requirement_bundle_assets.github_channel.get_file", _fake_get_file)
    monkeypatch.setattr("src.runtime.requirement_bundle_assets.github_channel.create_or_update_file", _fake_put_file)
    monkeypatch.setattr("src.agents.llm.LLMClient.chat", _fake_chat)

    result = await design_test_cases_skill.execute(
        bundle_ref={"repo": "acme/assets", "path": "requirement-bundles/payments/maker", "branch": "main"}
    )

    assert result.success is True
    assert ("requirement-bundles/payments/maker/requirements.yaml", "bundle/checkout/abcd1234") in reads
    assert writes and writes[0]["branch"] == "bundle/checkout/abcd1234"
    assert result.data["bundle_ref"]["branch"] == "bundle/checkout/abcd1234"


@pytest.mark.asyncio
async def test_collect_skill_reads_manifest_from_manifest_ref_and_writes_to_target_ref(monkeypatch):
    writes = []
    reads = []

    async def _fake_get_file(owner, repo, path, ref=""):
        reads.append((path, ref))
        if path.endswith("bundle.yaml") and ref == "main":
            yaml = _valid_manifest_yaml("rb-dual-collect").replace("working_branch: bundle/1", "working_branch: bundle/checkout/abcd1234")
            return {"content": _b64(yaml)}
        if path == "docs/spec.md" and ref == "bundle/checkout/abcd1234":
            return {"content": _b64("# Canonical Spec")}
        raise AssertionError((owner, repo, path, ref))

    async def _fake_put_file(owner, repo, path, content, message, sha=None, branch=""):
        writes.append({"path": path, "branch": branch})
        return {"commit": {"sha": "sha-dual-collect"}}

    async def _fake_chat(self, **_kwargs):
        return {
            "content": "{\"summary\":{},\"functional_requirements\":[\"FR-1\"],\"business_rules\":[],\"acceptance_criteria\":[],\"edge_cases\":[],\"quality_flags\":{\"ambiguities\":[],\"conflicts\":[],\"missing_information\":[]}}"
        }

    async def _fake_text(*args, **kwargs):
        return "ok"

    monkeypatch.setattr("skills.collect_requirements_to_bundle.skill.github_channel.is_configured", lambda: True)
    monkeypatch.setattr("src.runtime.requirement_bundle_assets.github_channel.get_file", _fake_get_file)
    monkeypatch.setattr("src.runtime.requirement_bundle_assets.github_channel.create_or_update_file", _fake_put_file)
    monkeypatch.setattr("skills.collect_requirements_to_bundle.skill.jira_get_issue", _fake_text)
    monkeypatch.setattr("skills.collect_requirements_to_bundle.skill.jira_get_issue_by_url", _fake_text)
    monkeypatch.setattr("skills.collect_requirements_to_bundle.skill.confluence_get_page", _fake_text)
    monkeypatch.setattr("skills.collect_requirements_to_bundle.skill.confluence_get_page_by_url", _fake_text)
    monkeypatch.setattr("src.agents.llm.LLMClient.chat", _fake_chat)

    result = await collect_requirements_skill.execute(
        bundle_ref={"repo": "acme/assets", "path": "requirement-bundles/payments/maker", "branch": "bundle/checkout/abcd1234"},
        manifest_ref={"repo": "acme/assets", "path": "requirement-bundles/payments/maker", "branch": "main"},
        sources={"github_docs": ["docs/spec.md"]},
    )

    assert result.success is True
    assert ("requirement-bundles/payments/maker/bundle.yaml", "main") in reads
    assert writes and writes[0]["branch"] == "bundle/checkout/abcd1234"
    assert result.data["bundle_ref"]["branch"] == "bundle/checkout/abcd1234"


@pytest.mark.asyncio
async def test_design_skill_reads_manifest_from_manifest_ref_and_reads_writes_target_ref(monkeypatch):
    writes = []
    reads = []

    async def _fake_get_file(owner, repo, path, ref=""):
        reads.append((path, ref))
        if path.endswith("bundle.yaml") and ref == "main":
            yaml = _valid_manifest_yaml("rb-dual-design").replace("working_branch: bundle/1", "working_branch: bundle/checkout/abcd1234")
            return {"content": _b64(yaml)}
        if path.endswith("requirements.yaml") and ref == "bundle/checkout/abcd1234":
            return {
                "content": _b64(
                    "bundle_id: rb-dual-design\nsources: {}\nsummary: {text: ok}\nfunctional_requirements: [FR-1]\nbusiness_rules: []\nacceptance_criteria: []\nedge_cases: []\nquality_flags:\n  ambiguities: []\n  conflicts: []\n  missing_information: []\n"
                )
            }
        raise AssertionError((owner, repo, path, ref))

    async def _fake_put_file(owner, repo, path, content, message, sha=None, branch=""):
        writes.append({"path": path, "branch": branch})
        return {"commit": {"sha": "sha-dual-design"}}

    async def _fake_chat(self, **_kwargs):
        return {
            "content": "{\"test_cases\":[{\"case_id\":\"TC-1\",\"title\":\"happy\",\"category\":\"functional\",\"priority\":\"P1\",\"preconditions\":[],\"steps\":[],\"expected_results\":[],\"traceability\":[\"FR-1\"]}]}"
        }

    monkeypatch.setattr("src.runtime.requirement_bundle_assets.github_channel.get_file", _fake_get_file)
    monkeypatch.setattr("src.runtime.requirement_bundle_assets.github_channel.create_or_update_file", _fake_put_file)
    monkeypatch.setattr("src.agents.llm.LLMClient.chat", _fake_chat)

    result = await design_test_cases_skill.execute(
        bundle_ref={"repo": "acme/assets", "path": "requirement-bundles/payments/maker", "branch": "bundle/checkout/abcd1234"},
        manifest_ref={"repo": "acme/assets", "path": "requirement-bundles/payments/maker", "branch": "main"},
    )

    assert result.success is True
    assert ("requirement-bundles/payments/maker/bundle.yaml", "main") in reads
    assert ("requirement-bundles/payments/maker/requirements.yaml", "bundle/checkout/abcd1234") in reads
    assert writes and writes[0]["branch"] == "bundle/checkout/abcd1234"
    assert result.data["bundle_ref"]["branch"] == "bundle/checkout/abcd1234"


@pytest.mark.asyncio
async def test_collect_skill_invalid_bundle_ref_returns_clear_error(monkeypatch):
    monkeypatch.setattr("skills.collect_requirements_to_bundle.skill.github_channel.is_configured", lambda: True)
    result = await collect_requirements_skill.execute(bundle_ref={"repo": "bad-format"}, sources={})
    assert result.success is False
    assert "owner/repo" in (result.error or "")


@pytest.mark.asyncio
async def test_collect_skill_empty_supported_sources_rejected(monkeypatch):
    async def _fake_get_file(owner, repo, path, ref=""):
        if path.endswith("bundle.yaml"):
            return {"content": _b64(_valid_manifest_yaml("rb-empty"))}
        raise AssertionError(path)

    monkeypatch.setattr("skills.collect_requirements_to_bundle.skill.github_channel.is_configured", lambda: True)
    monkeypatch.setattr("src.runtime.requirement_bundle_assets.github_channel.get_file", _fake_get_file)
    result = await collect_requirements_skill.execute(
        bundle_ref={"repo": "acme/assets", "path": "requirement-bundles/payments/maker", "branch": "bundle/1"},
        sources={},
    )
    assert result.success is False
    assert result.error == "At least one supported source is required"


@pytest.mark.asyncio
async def test_collect_skill_figma_only_rejected(monkeypatch):
    async def _fake_get_file(owner, repo, path, ref=""):
        if path.endswith("bundle.yaml"):
            return {"content": _b64(_valid_manifest_yaml("rb-figma"))}
        raise AssertionError(path)

    monkeypatch.setattr("skills.collect_requirements_to_bundle.skill.github_channel.is_configured", lambda: True)
    monkeypatch.setattr("src.runtime.requirement_bundle_assets.github_channel.get_file", _fake_get_file)
    result = await collect_requirements_skill.execute(
        bundle_ref={"repo": "acme/assets", "path": "requirement-bundles/payments/maker", "branch": "bundle/1"},
        sources={"figma": ["fig-1"]},
    )
    assert result.success is False
    assert "At least one supported source is required" in (result.error or "")
    assert "figma is ignored in MVP" in (result.error or "")


@pytest.mark.asyncio
async def test_collect_skill_invalid_manifest_returns_error(monkeypatch):
    async def _fake_get_file(owner, repo, path, ref=""):
        if path.endswith("bundle.yaml"):
            return {"content": _b64("bundle_id: rb-1\ntitle: bad\nstatus: draft\nscope: {}\nlinks: {}\n")}
        raise AssertionError(path)

    monkeypatch.setattr("skills.collect_requirements_to_bundle.skill.github_channel.is_configured", lambda: True)
    monkeypatch.setattr("src.runtime.requirement_bundle_assets.github_channel.get_file", _fake_get_file)

    result = await collect_requirements_skill.execute(
        bundle_ref={"repo": "acme/assets", "path": "requirement-bundles/payments/maker", "branch": "bundle/1"},
        sources={"jira": ["PAY-1"]},
    )
    assert result.success is False
    assert "bundle.yaml missing required field" in (result.error or "")


@pytest.mark.asyncio
async def test_design_skill_empty_requirements_rejected_without_llm(monkeypatch):
    llm_called = {"value": False}

    async def _fake_get_file(owner, repo, path, ref=""):
        if path.endswith("bundle.yaml"):
            return {"content": _b64(_valid_manifest_yaml("rb-3"))}
        if path.endswith("requirements.yaml"):
            return {
                "content": _b64(
                    "bundle_id: rb-3\nsources: {}\nsummary: {}\nfunctional_requirements: []\nbusiness_rules: []\nacceptance_criteria: []\nedge_cases: []\nquality_flags:\n  ambiguities: []\n  conflicts: []\n  missing_information: []\n"
                )
            }
        raise AssertionError(path)

    async def _fake_chat(self, **kwargs):
        llm_called["value"] = True
        return {"content": "{}"}

    monkeypatch.setattr("src.runtime.requirement_bundle_assets.github_channel.get_file", _fake_get_file)
    monkeypatch.setattr("src.agents.llm.LLMClient.chat", _fake_chat)

    result = await design_test_cases_skill.execute(
        bundle_ref={"repo": "acme/assets", "path": "requirement-bundles/payments/maker", "branch": "bundle/1"}
    )
    assert result.success is False
    assert "does not contain any designable requirement content" in (result.error or "")
    assert llm_called["value"] is False


def test_parse_bundle_ref_invalid_raises():
    with pytest.raises(RequirementBundleError):
        parse_bundle_ref({"repo": "missing", "path": "x", "branch": "b"})


def test_parse_github_doc_ref_blob_url():
    default_ref = parse_bundle_ref({"repo": "acme/assets", "path": "x", "branch": "bundle/1"})
    parsed = parse_github_doc_ref("https://github.com/org/repo/blob/main/docs/spec.md", default_ref)
    assert parsed.owner == "org"
    assert parsed.repo == "repo"
    assert parsed.branch == "main"
    assert parsed.path == "docs/spec.md"


def test_validate_bundle_manifest_rejects_blank_required_fields():
    blank_scope_summary = {
        "bundle_id": "rb-1",
        "title": "Maker Checker",
        "status": "draft",
        "scope": {"domain": "payments", "summary": "   "},
        "storage": {
            "repo": "acme/assets",
            "path": "requirement-bundles/payments/maker",
            "base_branch": "main",
            "working_branch": "bundle/1",
        },
        "links": {"requirements_file": "requirements.yaml", "test_cases_file": "test-cases.yaml"},
    }
    with pytest.raises(RequirementBundleError, match="scope.summary"):
        validate_bundle_manifest(blank_scope_summary)

    blank_working_branch = {
        "bundle_id": "rb-1",
        "title": "Maker Checker",
        "status": "draft",
        "scope": {"domain": "payments", "summary": "maker checker"},
        "storage": {
            "repo": "acme/assets",
            "path": "requirement-bundles/payments/maker",
            "base_branch": "main",
            "working_branch": "   ",
        },
        "links": {"requirements_file": "requirements.yaml", "test_cases_file": "test-cases.yaml"},
    }
    with pytest.raises(RequirementBundleError, match="storage.working_branch"):
        validate_bundle_manifest(blank_working_branch)


def test_resolve_bundle_links_uses_manifest_links():
    manifest = {
        "links": {
            "requirements_file": " docs/reqs.yaml ",
            "test_cases_file": "/outputs/tc.yaml/",
        }
    }

    requirements_file, test_cases_file = resolve_bundle_links(manifest)

    assert requirements_file == "docs/reqs.yaml"
    assert test_cases_file == "outputs/tc.yaml"


def test_build_test_design_context_is_trimmed():
    context = build_test_design_context(
        {"bundle_id": "rb-9", "title": "T", "scope": {"a": 1}, "other": "x"},
        {
            "summary": {"s": 1},
            "functional_requirements": [1],
            "business_rules": [2],
            "acceptance_criteria": [3],
            "edge_cases": [4],
            "quality_flags": {"ambiguities": [], "conflicts": [], "missing_information": []},
            "raw_blob": "should-not-be-included",
        },
    )

    assert set(context.keys()) == {
        "bundle_id",
        "title",
        "scope",
        "summary",
        "functional_requirements",
        "business_rules",
        "acceptance_criteria",
        "edge_cases",
        "quality_flags",
    }


def test_resolve_target_bundle_ref_prefers_manifest_storage():
    input_ref = parse_bundle_ref({"repo": "acme/assets", "path": "bundle/input", "branch": "main"})
    manifest = {
        "storage": {
            "repo": "org/target-repo",
            "path": "bundle/target",
            "working_branch": "bundle/checkout/abcd1234",
        }
    }

    resolved = resolve_target_bundle_ref(input_ref, manifest)

    assert resolved.repo_full_name == "org/target-repo"
    assert resolved.path == "bundle/target"
    assert resolved.branch == "bundle/checkout/abcd1234"
