import base64
import json

import pytest

from skills.collect_requirements_to_bundle.skill import collect_requirements_to_bundle as collect_requirements_skill
from skills.design_test_cases_from_bundle.skill import design_test_cases_from_bundle as design_test_cases_skill
from src.runtime.requirement_bundle_assets import (
    RequirementBundleError,
    build_test_design_context,
    parse_bundle_ref,
)


def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("utf-8")


@pytest.mark.asyncio
async def test_collect_skill_reads_manifest_and_writes_requirements(monkeypatch):
    writes = []

    async def _fake_get_file(owner, repo, path, ref=""):
        if path.endswith("bundle.yaml"):
            return {"content": _b64("bundle_id: rb-1\nscope:\n  product: payments\n")}
        if path.endswith("docs/spec.md"):
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

    async def _fake_jira_issue(issue_key):
        return {"key": issue_key, "fields": {"summary": "Jira item"}}

    async def _fake_confluence_page(page_id):
        return {"id": page_id, "title": "Confluence page"}

    monkeypatch.setattr("skills.collect_requirements_to_bundle.skill.github_channel.is_configured", lambda: True)
    monkeypatch.setattr("skills.collect_requirements_to_bundle.skill.github_channel.get_file", _fake_get_file)
    monkeypatch.setattr("src.runtime.requirement_bundle_assets.github_channel.get_file", _fake_get_file)
    monkeypatch.setattr("src.runtime.requirement_bundle_assets.github_channel.create_or_update_file", _fake_put_file)
    monkeypatch.setattr("src.agents.llm.LLMClient.chat", _fake_chat)
    monkeypatch.setattr("skills.collect_requirements_to_bundle.skill.jira_channel.get_issue", _fake_jira_issue)
    monkeypatch.setattr("skills.collect_requirements_to_bundle.skill.confluence_channel.get_page", _fake_confluence_page)

    result = await collect_requirements_skill.execute(
        bundle_ref={"repo": "acme/assets", "path": "requirement-bundles/payments/maker", "branch": "bundle/1"},
        sources={"jira": ["PAY-101"], "confluence": ["9876"], "github_docs": ["docs/spec.md"], "figma": ["fig-1"]},
    )

    assert result.success is True
    assert writes and writes[0]["branch"] == "bundle/1"
    assert writes[0]["path"].endswith("requirements.yaml")
    assert "figma sources are ignored in MVP" in result.data.get("warnings", [])


@pytest.mark.asyncio
async def test_design_skill_reads_requirements_and_writes_test_cases(monkeypatch):
    writes = []

    async def _fake_get_file(owner, repo, path, ref=""):
        if path.endswith("bundle.yaml"):
            return {"content": _b64("bundle_id: rb-2\nscope:\n  area: checkout\n")}
        if path.endswith("requirements.yaml"):
            return {
                "content": _b64(
                    "summary: {text: ok}\nfunctional_requirements: [FR-1]\nbusiness_rules: [BR-1]\nacceptance_criteria: [AC-1]\nedge_cases: [EC-1]\nquality_flags:\n  ambiguities: []\n  conflicts: []\n  missing_information: []\n"
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
    assert writes[0]["branch"] == "bundle/2"


@pytest.mark.asyncio
async def test_collect_skill_invalid_bundle_ref_returns_clear_error(monkeypatch):
    monkeypatch.setattr("skills.collect_requirements_to_bundle.skill.github_channel.is_configured", lambda: True)
    result = await collect_requirements_skill.execute(bundle_ref={"repo": "bad-format"}, sources={})
    assert result.success is False
    assert "owner/repo" in (result.error or "")


def test_parse_bundle_ref_invalid_raises():
    with pytest.raises(RequirementBundleError):
        parse_bundle_ref({"repo": "missing", "path": "x", "branch": "b"})


def test_build_test_design_context_is_trimmed():
    context = build_test_design_context(
        {"scope": {"a": 1}, "other": "x"},
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
        "scope",
        "summary",
        "functional_requirements",
        "business_rules",
        "acceptance_criteria",
        "edge_cases",
        "quality_flags",
    }
