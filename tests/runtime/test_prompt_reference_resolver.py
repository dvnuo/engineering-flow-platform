from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from efp_runtime.context import render_messages
from efp_runtime.llm.openai import provider_request_to_openai_chat
from efp_runtime.llm.request import ProviderRequest
from efp_runtime.loop import LoopStatus, ScriptedLLMProvider
from efp_runtime.models import Message, MessagePartType
from efp_runtime.prompt import resolve_prompt_references
from efp_runtime.runtime import AgentRuntime, RuntimeConfig


ROOT = Path(__file__).resolve().parents[2]


def test_file_reference_creates_text_and_attachment_part(tmp_path: Path):
    source = tmp_path / "src" / "app.py"
    source.parent.mkdir()
    source.write_text("print('hello runtime')\n", encoding="utf-8")

    resolved = resolve_prompt_references(
        "Please inspect @src/app.py.",
        workspace_root=tmp_path,
    )

    assert resolved.text == "Please inspect @src/app.py."
    assert [reference.kind for reference in resolved.references] == ["file"]
    assert resolved.references[0].raw == "@src/app.py"
    assert resolved.references[0].path == "src/app.py"
    assert [part.type for part in resolved.parts] == [
        MessagePartType.TEXT,
        MessagePartType.ATTACHMENT,
    ]
    assert resolved.parts[0].text == "Please inspect @src/app.py."

    attachment = resolved.parts[1].attachment
    assert attachment is not None
    assert attachment.mime_type == "text/plain"
    assert attachment.filename == "app.py"
    assert attachment.text_ref == "src/app.py"
    assert attachment.metadata["kind"] == "prompt_reference"
    assert attachment.metadata["path"] == "src/app.py"
    assert attachment.metadata["content"] == "print('hello runtime')\n"
    assert attachment.metadata["truncated"] is False
    assert attachment.metadata["original_chars"] == len("print('hello runtime')\n")


def test_directory_reference_creates_visible_limited_listing(tmp_path: Path):
    project_dir = tmp_path / "pkg"
    project_dir.mkdir()
    (project_dir / "a.py").write_text("a", encoding="utf-8")
    (project_dir / "b.py").write_text("b", encoding="utf-8")
    (project_dir / "c.py").write_text("c", encoding="utf-8")

    resolved = resolve_prompt_references(
        "Review @pkg",
        workspace_root=tmp_path,
        max_directory_entries=2,
    )

    assert [reference.kind for reference in resolved.references] == ["directory"]
    attachment = resolved.parts[1].attachment
    assert attachment is not None
    assert attachment.text_ref == "pkg"
    assert attachment.metadata["entry_count"] == 3
    assert attachment.metadata["truncated"] is True
    assert [entry["name"] for entry in attachment.metadata["entries"]] == ["a.py", "b.py"]

    rendered = render_messages(
        [Message(role="user", message_id="msg-user", parts=resolved.parts)]
    )[0]
    payload = provider_request_to_openai_chat(
        ProviderRequest(messages=[rendered]),
        model="gpt-test",
    )
    content = payload["messages"][0]["content"]
    assert "a.py" in content
    assert "b.py" in content
    assert "entry_count" in content
    assert "directory listing truncated" in content


def test_missing_and_outside_references_create_error_parts(tmp_path: Path):
    resolved = resolve_prompt_references(
        "Read @missing.txt and @../outside.txt",
        workspace_root=tmp_path,
    )

    assert [reference.kind for reference in resolved.references] == ["missing", "outside"]
    assert [part.type for part in resolved.parts] == [
        MessagePartType.TEXT,
        MessagePartType.ERROR,
        MessagePartType.ERROR,
    ]
    assert resolved.parts[1].metadata == {
        "kind": "prompt_reference_error",
        "raw": "@missing.txt",
        "path": "missing.txt",
        "reason": "missing",
    }
    assert resolved.parts[2].metadata["kind"] == "prompt_reference_error"
    assert resolved.parts[2].metadata["raw"] == "@../outside.txt"
    assert resolved.parts[2].metadata["reason"] == "outside_workspace"


def test_email_urls_and_bare_at_are_not_references(tmp_path: Path):
    resolved = resolve_prompt_references(
        (
            "Email dev@example.com, keep @, visit https://example.com/@src/app.py, "
            "and ignore @https://example.com/src/app.py plus @person@example.com."
        ),
        workspace_root=tmp_path,
    )

    assert resolved.references == []
    assert len(resolved.parts) == 1
    assert resolved.parts[0].type is MessagePartType.TEXT


@pytest.mark.asyncio
async def test_agent_runtime_writes_prompt_reference_parts_to_history_and_request(
    tmp_path: Path,
):
    notes = tmp_path / "notes.txt"
    notes.write_text("alpha\nbeta\n", encoding="utf-8")
    provider = ScriptedLLMProvider([{"content": "Done."}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(workspace_root=tmp_path, max_iterations=1),
    )

    result = await runtime.run("Use @notes.txt", session_id="session-prompt-ref")

    assert result.status == LoopStatus.COMPLETED
    history = runtime.store.read_history("session-prompt-ref")
    assert [part.type for part in history[0].parts] == [
        MessagePartType.TEXT,
        MessagePartType.ATTACHMENT,
    ]
    assert history[0].parts[1].attachment is not None
    assert history[0].parts[1].attachment.metadata["content"] == "alpha\nbeta\n"

    request_message = provider.requests[0].provider_request.messages[-1]
    assert request_message.role == "user"
    assert request_message.parts[0].text == "Use @notes.txt"
    assert request_message.attachments[0].text_ref == "notes.txt"
    assert request_message.parts[1].context is not None
    assert request_message.parts[1].context.metadata["metadata"]["content"] == "alpha\nbeta\n"

    payload = provider_request_to_openai_chat(
        provider.requests[0].provider_request,
        model="gpt-test",
    )
    assert any("alpha" in message["content"] for message in payload["messages"])


@pytest.mark.asyncio
async def test_agent_runtime_can_keep_prompt_references_as_plain_text(tmp_path: Path):
    (tmp_path / "notes.txt").write_text("alpha\n", encoding="utf-8")
    provider = ScriptedLLMProvider([{"content": "Done."}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            workspace_root=tmp_path,
            max_iterations=1,
            resolve_prompt_references=False,
        ),
    )

    await runtime.run("Use @notes.txt", session_id="session-plain")

    history = runtime.store.read_history("session-plain")
    assert [part.type for part in history[0].parts] == [MessagePartType.TEXT]
    assert history[0].parts[0].text == "Use @notes.txt"
    request_message = provider.requests[0].provider_request.messages[-1]
    assert request_message.text == "Use @notes.txt"
    assert request_message.attachments == []


def test_prompt_resolver_imports_standalone_with_pythonpath_src():
    code = """
import json
import sys

import efp_runtime.prompt

print(json.dumps({"legacy_core_loaded": "src.agents.core" in sys.modules}))
"""
    env = dict(os.environ)
    env["PYTHONPATH"] = "src"
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload == {"legacy_core_loaded": False}


def test_prompt_resolver_source_stays_inside_runtime_boundary():
    combined = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((ROOT / "src/efp_runtime/prompt").rglob("*.py"))
    )
    forbidden_tokens = [
        "from src.efp_runtime",
        "import src.efp_runtime",
        "from src.agents.core",
        "import src.agents.core",
        "from src.runtime",
        "import src.runtime",
        "from src.sessions",
        "import src.sessions",
        "from src.skills",
        "import src.skills",
    ]
    for token in forbidden_tokens:
        assert token not in combined
