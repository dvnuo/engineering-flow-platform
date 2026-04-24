import pytest


@pytest.mark.asyncio
async def test_bundle_skill_loaders_return_structured_jira_and_confluence_sources(monkeypatch):
    from skills.collect_requirements_to_bundle.skill import _load_confluence_sources, _load_jira_sources

    class _JiraPrepared:
        issue_key = "P-1"
        bundle = {"artifact_refs": [{"artifact_id": "jira-a1"}]}
        manifest = {"context_ref": "jira-ctx", "digest_ref": "jira-dig"}

    async def _fake_prepare_jira(source, session_id=None):
        return _JiraPrepared()

    async def _fake_prepare_confluence(source, session_id=None):
        return {
            "page_id": "42",
            "manifest": {"context_ref": "conf-ctx", "digest_ref": "conf-dig"},
            "artifact_refs": [{"artifact_id": "conf-a1"}],
        }

    monkeypatch.setattr("skills.collect_requirements_to_bundle.skill.prepare_jira_issue_source", _fake_prepare_jira)
    monkeypatch.setattr("skills.collect_requirements_to_bundle.skill.format_jira_source_manifest", lambda prepared: "jira-manifest")
    monkeypatch.setattr("skills.collect_requirements_to_bundle.skill.prepare_confluence_page_source", _fake_prepare_confluence)
    monkeypatch.setattr("skills.collect_requirements_to_bundle.skill.format_confluence_source_manifest", lambda prepared: "confluence-manifest")

    jira_items = await _load_jira_sources(["P-1"], session_id="s1")
    conf_items = await _load_confluence_sources(["42"], session_id="s1")

    assert jira_items[0]["content"] == "jira-manifest"
    assert jira_items[0]["source_kind"] == "jira_issue"
    assert jira_items[0]["artifact_refs"] == [{"artifact_id": "jira-a1"}]
    assert jira_items[0]["context_ref"] == "jira-ctx"
    assert jira_items[0]["digest_ref"] == "jira-dig"

    assert conf_items[0]["content"] == "confluence-manifest"
    assert conf_items[0]["source_kind"] == "confluence_page"
    assert conf_items[0]["artifact_refs"] == [{"artifact_id": "conf-a1"}]
    assert conf_items[0]["context_ref"] == "conf-ctx"
    assert conf_items[0]["digest_ref"] == "conf-dig"
