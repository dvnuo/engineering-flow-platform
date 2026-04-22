import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.jira.exporter import _resolve_output_directory, export_issues_to_markdown
from src.jira.selector import (
    extract_issue_keys_from_text,
    extract_output_directory_from_text,
    normalize_jira_issue_selector,
)
from src.jira.source_service import JiraIssueSourceResult
from src.jira.markdown_renderer import render_jira_issue_export_markdown


EXACT_PROMPT = """帮我把下面jira ticket 转成markdown, 并save到folder：/root/.efp/workspace/FXOW/FXLanding，如果ticket有attachment，请一并下载
MMGFX-14839
MMGFX-14838
MMGFX-14833
MMGFX-14832"""


def test_selector_extracts_exact_user_prompt():
    keys = extract_issue_keys_from_text(EXACT_PROMPT)
    assert keys == ["MMGFX-14839", "MMGFX-14838", "MMGFX-14833", "MMGFX-14832"]
    assert extract_output_directory_from_text(EXACT_PROMPT) == "/root/.efp/workspace/FXOW/FXLanding"
    selector = normalize_jira_issue_selector(input=EXACT_PROMPT)
    assert selector["selector_type"] == "issue_keys"
    assert selector["issue_keys"] == keys


def test_json_string_input_list():
    selector = normalize_jira_issue_selector(input='["MMGFX-14839","MMGFX-14838"]')
    assert selector["issue_keys"] == ["MMGFX-14839", "MMGFX-14838"]


def test_json_string_input_jql():
    selector = normalize_jira_issue_selector(input='{"jql":"project = MMGFX","page_size":25}')
    assert selector["selector_type"] == "jql"
    assert selector["jql"] == "project = MMGFX"
    assert selector["page_size"] == 25


def test_output_directory_workspace_guard(tmp_path, monkeypatch):
    home = tmp_path / "home"
    root_workspace = tmp_path / "root" / ".efp" / "workspace"
    env_workspace = tmp_path / "env-workspace"

    monkeypatch.setattr("pathlib.Path.home", lambda: home)
    monkeypatch.setenv("EFP_WORKSPACE_ROOT", str(env_workspace))

    ok1 = _resolve_output_directory(str(home / ".efp" / "workspace" / "FXOW"))
    assert ok1.exists()

    ok2 = _resolve_output_directory(str(env_workspace / "FXOW"))
    assert ok2.exists()

    monkeypatch.setattr("src.jira.exporter._allowed_workspace_roots", lambda: [root_workspace.resolve()])
    ok3 = _resolve_output_directory(str(root_workspace / "FXOW" / "FXLanding"))
    assert ok3.exists()

    with pytest.raises(ValueError):
        _resolve_output_directory(str(tmp_path / "etc"))
    with pytest.raises(ValueError):
        _resolve_output_directory(str(root_workspace / ".." / ".ssh"))


@pytest.mark.asyncio
async def test_export_exact_prompt_writes_markdown_and_attachments(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setattr("pathlib.Path.home", lambda: home)
    prompt = EXACT_PROMPT.replace("/root", str(home))

    class FakeAdapter:
        def _strip_acceptance_criteria_from_markdown_description(self, text):
            return text.replace("## Acceptance Criteria\n- AC", "").strip()

        def _convert_description_to_markdown(self, body):
            return str(body or "")

    async def fake_prepare(issue_key_or_url, **kwargs):
        key = issue_key_or_url
        fields = {"summary": f"Summary {key}", "attachment": [{"filename": "note.txt", "mimeType": "text/plain", "size": 10, "content": "http://x"}]}
        bundle = {
            "metadata": {"key": key, "title": f"Summary {key}", "status": "Open", "type": "Task", "priority": "P1", "assignee": "A"},
            "description": "Desc\n## Acceptance Criteria\n- AC",
            "acceptance_criteria": "- AC",
            "comments": [
                {"author": {"displayName": "U1"}, "created": "2026-01-01", "body": "c1", "body_markdown": "c1"},
            ],
            "attachments": [{"filename": "note.txt", "size": 10, "mime_type": "text/plain"}],
            "completeness_ledger": {"comments_loaded": 1},
            "raw_snapshot": {"k": "v"},
        }
        manifest = {"context_ref": f"ctx-{key}", "digest_ref": f"dig-{key}", "source_complete": True, "source_complete_for_generation": True, "partial_reasons": []}
        return JiraIssueSourceResult(
            issue_key=key,
            issue={"key": key, "fields": fields},
            fields=fields,
            bundle=bundle,
            manifest=manifest,
            persisted={},
            channel=SimpleNamespace(),
            adapter=FakeAdapter(),
            attachment_list=fields["attachment"],
        )

    async def fake_download(*args, **kwargs):
        output_dir = kwargs["output_dir"]
        issue_key = args[0]
        p = output_dir / "attachments" / issue_key / "note.txt"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("hello", encoding="utf-8")
        return [{"filename": "note.txt", "status": "saved", "path": f"attachments/{issue_key}/note.txt", "absolute_path": str(p), "size": 5, "mime_type": "text/plain"}]

    monkeypatch.setattr("src.jira.exporter.prepare_jira_issue_source", fake_prepare)
    monkeypatch.setattr("src.jira.exporter._download_issue_attachments", fake_download)

    result = await export_issues_to_markdown(input=prompt, _session_id="test-session")
    assert result["status"] == "success"
    assert len(result["issues"]) == 4
    manifest_path = Path(result["artifacts"]["manifest_path"])
    assert manifest_path.exists()
    first_issue = result["issues"][0]
    md_path = Path(first_issue["markdown_path"])
    assert md_path.exists()
    text = md_path.read_text(encoding="utf-8")
    assert "## Description" in text
    assert "## Acceptance Criteria" in text
    assert "## Comments" in text
    assert "## Attachments" in text
    assert f"attachments/{first_issue['issue_key']}/note.txt" in text
    assert (manifest_path.parent / "attachments" / first_issue["issue_key"] / "note.txt").exists()


def test_comments_latest_first_sorts_before_truncating():
    source = JiraIssueSourceResult(
        issue_key="MMGFX-1",
        issue={},
        fields={"summary": "S"},
        bundle={
            "metadata": {"title": "S"},
            "description": "D",
            "acceptance_criteria": "AC",
            "comments": [
                {"author": "A", "created": "2026-01-01", "body_markdown": "old"},
                {"author": "A", "created": "2026-01-03", "body_markdown": "newest"},
                {"author": "A", "created": "2026-01-02", "body_markdown": "mid"},
            ],
            "attachments": [],
            "completeness_ledger": {},
        },
        manifest={},
        persisted={},
        channel=SimpleNamespace(),
        adapter=SimpleNamespace(_strip_acceptance_criteria_from_markdown_description=lambda x: x, _convert_description_to_markdown=lambda x: x),
        attachment_list=[],
    )
    md = render_jira_issue_export_markdown(source, max_comments=2, comments_order="latest_first")
    assert md.index("newest") < md.index("mid")
    assert "old" not in md


def test_raw_snapshot_is_json_fenced_block():
    source = JiraIssueSourceResult(
        issue_key="MMGFX-1",
        issue={},
        fields={"summary": "S"},
        bundle={"metadata": {"title": "S"}, "description": "D", "acceptance_criteria": "AC", "comments": [], "attachments": [], "completeness_ledger": {}, "raw_snapshot": {"a": 1}},
        manifest={}, persisted={}, channel=SimpleNamespace(),
        adapter=SimpleNamespace(_strip_acceptance_criteria_from_markdown_description=lambda x: x, _convert_description_to_markdown=lambda x: x),
        attachment_list=[],
    )
    md = render_jira_issue_export_markdown(source, include_raw_snapshot=True)
    assert "```json" in md
    assert "{'a': 1}" not in md


@pytest.mark.asyncio
async def test_zip_includes_attachments_and_manifest(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setattr("pathlib.Path.home", lambda: home)
    out = home / ".efp" / "workspace" / "FXOW" / "FXLanding"

    class FakeAdapter:
        def _strip_acceptance_criteria_from_markdown_description(self, text): return text
        def _convert_description_to_markdown(self, body): return str(body or "")

    async def fake_prepare(issue_key_or_url, **kwargs):
        key = issue_key_or_url
        fields = {"summary": "S", "attachment": [{"filename": "a.txt", "mimeType": "text/plain", "size": 10, "content": "http://x"}]}
        bundle = {"metadata": {"title": "S"}, "description": "D", "acceptance_criteria": "AC", "comments": [], "attachments": [], "completeness_ledger": {}}
        return JiraIssueSourceResult(issue_key=key, issue={}, fields=fields, bundle=bundle, manifest={"context_ref": "c", "digest_ref": "d", "source_complete": True, "source_complete_for_generation": True, "partial_reasons": []}, persisted={}, channel=SimpleNamespace(), adapter=FakeAdapter(), attachment_list=fields["attachment"])

    async def fake_download(*args, **kwargs):
        output_dir = kwargs["output_dir"]
        issue_key = args[0]
        p = output_dir / "attachments" / issue_key / "a.txt"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x", encoding="utf-8")
        return [{"filename": "a.txt", "status": "saved", "path": f"attachments/{issue_key}/a.txt", "absolute_path": str(p), "size": 1, "mime_type": "text/plain"}]

    monkeypatch.setattr("src.jira.exporter.prepare_jira_issue_source", fake_prepare)
    monkeypatch.setattr("src.jira.exporter._download_issue_attachments", fake_download)

    result = await export_issues_to_markdown(input=["MMGFX-1"], output_mode="zip", output_directory=str(out))
    zp = Path(result["artifacts"]["zip_path"])
    assert zp.exists()
    with zipfile.ZipFile(zp) as zf:
        names = set(zf.namelist())
        assert "manifest.json" in names
        assert any(name.endswith(".md") for name in names)
        assert "attachments/MMGFX-1/a.txt" in names


@pytest.mark.asyncio
async def test_manifest_contains_final_artifacts(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setattr("pathlib.Path.home", lambda: home)
    out = home / ".efp" / "workspace" / "FXOW" / "FXLanding"

    class FakeAdapter:
        def _strip_acceptance_criteria_from_markdown_description(self, text): return text
        def _convert_description_to_markdown(self, body): return str(body or "")

    async def fake_prepare(issue_key_or_url, **kwargs):
        key = issue_key_or_url
        fields = {"summary": "S", "attachment": [{"filename": "a.txt", "mimeType": "text/plain", "size": 10, "content": "http://x"}]}
        bundle = {"metadata": {"title": "S"}, "description": "D", "acceptance_criteria": "AC", "comments": [], "attachments": [], "completeness_ledger": {}}
        return JiraIssueSourceResult(issue_key=key, issue={}, fields=fields, bundle=bundle, manifest={"context_ref": "c", "digest_ref": "d", "source_complete": True, "source_complete_for_generation": True, "partial_reasons": []}, persisted={}, channel=SimpleNamespace(), adapter=FakeAdapter(), attachment_list=fields["attachment"])

    async def fake_download(*args, **kwargs):
        output_dir = kwargs["output_dir"]
        issue_key = args[0]
        p = output_dir / "attachments" / issue_key / "a.txt"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x", encoding="utf-8")
        return [{"filename": "a.txt", "status": "saved", "path": f"attachments/{issue_key}/a.txt", "absolute_path": str(p), "size": 1, "mime_type": "text/plain"}]

    monkeypatch.setattr("src.jira.exporter.prepare_jira_issue_source", fake_prepare)
    monkeypatch.setattr("src.jira.exporter._download_issue_attachments", fake_download)

    result = await export_issues_to_markdown(input=["MMGFX-1"], output_mode="zip", output_directory=str(out))
    manifest = json.loads(Path(result["artifacts"]["manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["artifacts"]["manifest_path"] == result["artifacts"]["manifest_path"]
    assert manifest["artifacts"]["zip_path"] == result["artifacts"]["zip_path"]
    assert manifest["output_directory"] == result["output_directory"]


@pytest.mark.asyncio
async def test_export_uses_metadata_only_source_policy_when_downloading_attachments(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setattr("pathlib.Path.home", lambda: home)
    out = home / ".efp" / "workspace" / "FXOW" / "FXLanding"
    captured = {}

    class FakeAdapter:
        def _strip_acceptance_criteria_from_markdown_description(self, text): return text
        def _convert_description_to_markdown(self, body): return str(body or "")

    async def fake_prepare(issue_key_or_url, **kwargs):
        captured[issue_key_or_url] = kwargs
        fields = {"summary": "S", "attachment": []}
        bundle = {"metadata": {"title": "S"}, "description": "D", "acceptance_criteria": "AC", "comments": [], "attachments": [], "completeness_ledger": {}}
        return JiraIssueSourceResult(issue_key=issue_key_or_url, issue={}, fields=fields, bundle=bundle, manifest={"context_ref": "c", "digest_ref": "d", "source_complete": True, "source_complete_for_generation": True, "partial_reasons": []}, persisted={}, channel=SimpleNamespace(is_configured=lambda: True, _auth_header={}), adapter=FakeAdapter(), attachment_list=[])

    monkeypatch.setattr("src.jira.exporter.prepare_jira_issue_source", fake_prepare)

    await export_issues_to_markdown(input=["MMGFX-1"], output_directory=str(out), download_attachments=True)
    assert captured["MMGFX-1"]["attachment_body_policy"] == "metadata_only"

    await export_issues_to_markdown(input=["MMGFX-1"], download_attachments=False)
    assert captured["MMGFX-1"]["attachment_body_policy"] == "source_complete"


@pytest.mark.asyncio
async def test_download_attachments_uses_source_channel_auth_header(tmp_path, monkeypatch):
    from src.jira.exporter import _download_issue_attachments

    source_file = tmp_path / "stored.txt"
    source_file.write_text("x", encoding="utf-8")
    seen = {}

    async def fake_download_and_process_attachment(url, session_id=None, options=None, auth_header=None):
        seen["auth_header"] = auth_header
        return SimpleNamespace(file_id="fid", filename="a.txt", metadata={"size": 1}, content_type="text/plain", content_format="text", content="x")

    monkeypatch.setattr("src.jira.exporter.download_and_process_attachment", fake_download_and_process_attachment)
    monkeypatch.setattr("src.jira.exporter.get_file_path", lambda file_id: source_file)

    output_dir = tmp_path / "out"
    source_channel = SimpleNamespace(is_configured=lambda: True, _auth_header={"Authorization": "Basic source"})
    await _download_issue_attachments(
        "MMGFX-1",
        [{"filename": "a.txt", "mimeType": "text/plain", "size": 1, "content": "http://x"}],
        output_dir=output_dir,
        attachments_dir="attachments",
        concurrency=1,
        attachments_max_size=1024,
        attachments_inline_text_threshold=100,
        attachments_retries=1,
        attachments_backoff=[0],
        attachments_preserve_binary=True,
        source_channel=source_channel,
    )
    assert seen["auth_header"] == {"Authorization": "Basic source"}
