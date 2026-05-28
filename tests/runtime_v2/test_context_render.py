import json
import os
import subprocess
import sys
from pathlib import Path

from efp_runtime.context import (
    prepare_history_for_request,
    render_history,
    render_messages,
    render_tool_schemas,
)
from efp_runtime.models import (
    Attachment,
    CompactionPart,
    Message,
    MessagePart,
    Session,
    ToolCall,
    ToolResult,
)
from efp_runtime.tools.definition import ToolDef


ROOT = Path(__file__).resolve().parents[2]


async def _execute(args, context):
    return args


def test_text_only_history_renders_request_messages():
    session = Session(
        session_id="session-1",
        title="Render history",
        messages=[
            Message.from_text(
                "system",
                "You are precise.",
                session_id="session-1",
                message_id="msg-system",
            ),
            Message.from_text(
                "user",
                "Summarize runtime v2.",
                session_id="session-1",
                message_id="msg-user",
            ),
            Message.from_text(
                "assistant",
                "Runtime v2 keeps typed history.",
                session_id="session-1",
                message_id="msg-assistant",
            ),
        ],
    )

    request = render_history(session, metadata={"request_id": "request-1"})

    assert request.metadata["session_id"] == "session-1"
    assert request.metadata["request_id"] == "request-1"
    assert [message.role for message in request.messages] == ["system", "user", "assistant"]
    assert [message.text for message in request.messages] == [
        "You are precise.",
        "Summarize runtime v2.",
        "Runtime v2 keeps typed history.",
    ]
    assert request.messages[1].parts[0].metadata["source_message_id"] == "msg-user"


def test_assistant_tool_call_and_tool_result_render_as_structured_parts():
    call = ToolCall(
        tool_name="search",
        arguments={"query": "runtime v2"},
        call_id="call-1",
        metadata={"origin": "assistant"},
    )
    result = ToolResult(
        call_id="call-1",
        tool_name="search",
        status="success",
        content="found runtime v2 notes",
        output={"matches": 1},
    )
    messages = [
        Message(
            role="assistant",
            message_id="msg-assistant",
            parts=[
                MessagePart.text_part("I will search."),
                MessagePart.reasoning_part("Need current notes."),
                MessagePart.tool_call_part(call),
            ],
        ),
        Message(
            role="tool",
            message_id="msg-tool",
            parts=[MessagePart.tool_result_part(result)],
        ),
    ]

    rendered = render_messages(messages)

    assistant = rendered[0]
    assert [part.type for part in assistant.parts] == ["text", "reasoning", "tool_call"]
    assert assistant.text == "I will search."
    assert assistant.reasoning[0].text == "Need current notes."
    assert assistant.tool_calls[0].call_id == "call-1"
    assert assistant.tool_calls[0].tool_name == "search"
    assert assistant.tool_calls[0].arguments == {"query": "runtime v2"}
    assert assistant.tool_calls[0].metadata["tool_call_metadata"] == {"origin": "assistant"}

    tool = rendered[1]
    assert tool.role == "tool"
    assert tool.tool_results[0].call_id == "call-1"
    assert tool.tool_results[0].tool_name == "search"
    assert tool.tool_results[0].content == "found runtime v2 notes"
    assert tool.tool_results[0].output == {"matches": 1}


def test_compaction_part_renders_as_system_context_summary():
    compaction = CompactionPart(
        summary="Earlier work: runtime v2 baseline was created.",
        source_message_ids=["msg-old"],
        auto=True,
        original_part_count=3,
        original_message_count=2,
    )
    message = Message(
        role="user",
        message_id="msg-compact",
        parts=[MessagePart.compaction_part(compaction)],
    )

    rendered = render_messages([message])

    assert len(rendered) == 1
    assert rendered[0].role == "system"
    assert rendered[0].text == ""
    assert rendered[0].metadata["rendered_as"] == "system_context"
    context = rendered[0].context[0]
    assert context.type == "compaction_summary"
    assert context.text == "Earlier work: runtime v2 baseline was created."
    assert context.metadata["source_role"] == "user"
    assert context.metadata["compaction"]["source_message_ids"] == ["msg-old"]


def test_attachments_render_as_context_metadata_and_text_refs():
    attachment = Attachment(
        attachment_id="att-1",
        mime_type="text/plain",
        filename="notes.txt",
        text_ref="blob:notes",
        metadata={"source": "upload"},
    )
    message = Message(
        role="user",
        message_id="msg-user",
        parts=[
            MessagePart.text_part("Use the uploaded notes."),
            MessagePart.attachment_part(attachment),
        ],
    )

    rendered = render_messages([message])[0]

    assert rendered.text == "Use the uploaded notes."
    assert rendered.attachments[0].attachment_id == "att-1"
    assert rendered.attachments[0].text_ref == "blob:notes"
    attachment_context = rendered.parts[1].context
    assert attachment_context is not None
    assert attachment_context.type == "attachment"
    assert attachment_context.text is None
    assert attachment_context.metadata["filename"] == "notes.txt"
    assert attachment_context.metadata["text_ref"] == "blob:notes"
    assert attachment_context.metadata["metadata"] == {"source": "upload"}


def test_tool_defs_render_to_provider_neutral_schema():
    tool = ToolDef(
        id="search",
        description="Search indexed project notes.",
        input_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
        execute=_execute,
        metadata={"category": "retrieval"},
    )

    schemas = render_tool_schemas([tool])

    assert len(schemas) == 1
    assert schemas[0].id == "search"
    assert schemas[0].name == "search"
    assert schemas[0].description == "Search indexed project notes."
    assert schemas[0].json_schema["properties"]["query"]["type"] == "string"
    assert schemas[0].metadata["definition_metadata"] == {"category": "retrieval"}
    assert schemas[0].metadata["permission"]["action"] == "allow"
    assert schemas[0].metadata["output_policy"]["include_raw_output"] is True


def test_prepare_history_compacts_when_max_parts_is_exceeded():
    messages = [
        Message.from_text("user", "old request"),
        Message.from_text("assistant", "old answer"),
        Message.from_text("user", "latest request"),
    ]

    prepared = prepare_history_for_request(messages, max_parts=2)

    assert prepared.compaction_applied is True
    assert prepared.compaction_metadata["compacted_part_count"] == 2
    assert prepared.request.metadata["compaction"]["max_parts"] == 2
    assert [message.role for message in prepared.request.messages] == ["system", "user"]
    assert prepared.request.messages[0].context[0].type == "compaction_summary"
    assert prepared.request.messages[1].text == "latest request"


def test_context_renderer_imports_standalone_with_pythonpath_src():
    code = """
import json
import sys

from efp_runtime.context import render_history
from efp_runtime.llm.request import ProviderRequest

print(json.dumps({
    "legacy_core_loaded": "src.agents.core" in sys.modules,
    "render_history": callable(render_history),
    "request_model": ProviderRequest.__name__,
}))
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
    assert payload == {
        "legacy_core_loaded": False,
        "render_history": True,
        "request_model": "ProviderRequest",
    }


def test_context_renderer_source_stays_inside_v2_import_boundary():
    combined = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((ROOT / "src/efp_runtime").rglob("*.py"))
    )
    forbidden_tokens = [
        "from src.efp_runtime",
        "import src.efp_runtime",
        "from src.agents.core",
        "import src.agents.core",
        "from src.agents.tool_result_policy",
        "import src.agents.tool_result_policy",
        "Agent.process(",
        "SkillSession(",
        "SkillsExecutor(",
    ]
    for token in forbidden_tokens:
        assert token not in combined

