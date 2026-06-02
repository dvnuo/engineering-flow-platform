import json
import os
import subprocess
import sys
from pathlib import Path

from efp_runtime.llm.openai import (
    provider_request_to_openai_chat,
    provider_request_to_openai_responses,
    request_tool_schema_to_openai_tool,
)
from efp_runtime.llm.request import (
    ProviderRequest,
    RequestAttachment,
    RequestContext,
    RequestMessage,
    RequestMessagePart,
    RequestToolCall,
    RequestToolResult,
    RequestToolSchema,
)


ROOT = Path(__file__).resolve().parents[2]


def test_chat_projection_keeps_text_context_attachment_and_system_compaction():
    request = ProviderRequest(
        messages=[
            RequestMessage(
                role="system",
                parts=[
                    RequestMessagePart(
                        type="context",
                        context=RequestContext(
                            type="compaction_summary",
                            text="Earlier work: EFP runtime baseline was created.",
                            metadata={"source_role": "user", "source_message_ids": ["msg-old"]},
                        ),
                    )
                ],
                metadata={"rendered_as": "system_context"},
            ),
            RequestMessage(
                role="user",
                parts=[
                    RequestMessagePart(type="text", text="Use the uploaded notes."),
                    RequestMessagePart(
                        type="context",
                        context=RequestContext(
                            type="task",
                            text="Summarize the implementation plan.",
                            metadata={"task": {"task_id": "task-1"}},
                        ),
                    ),
                    RequestMessagePart(
                        type="attachment",
                        attachment=RequestAttachment(
                            attachment_id="att-1",
                            mime_type="text/plain",
                            filename="notes.txt",
                            text_ref="blob:notes",
                            metadata={"source": "upload"},
                        ),
                    ),
                ],
            ),
        ],
        metadata={"request_id": "request-1"},
    )

    payload = provider_request_to_openai_chat(
        request,
        model="gpt-test",
        instructions="Be precise.",
        stream=True,
        metadata={"trace_id": "trace-1"},
    )

    assert payload["model"] == "gpt-test"
    assert payload["stream"] is True
    assert payload["metadata"]["request_id"] == "request-1"
    assert payload["metadata"]["trace_id"] == "trace-1"
    assert payload["messages"][0] == {"role": "system", "content": "Be precise."}

    compaction = payload["messages"][1]
    assert compaction["role"] == "system"
    assert "[context:compaction_summary]" in compaction["content"]
    assert "Earlier work: EFP runtime baseline was created." in compaction["content"]

    user_message = payload["messages"][2]
    assert user_message["role"] == "user"
    assert "Use the uploaded notes." in user_message["content"]
    assert "[context:task]" in user_message["content"]
    assert "Summarize the implementation plan." in user_message["content"]
    assert "[attachment:att-1]" in user_message["content"]
    assert "filename: notes.txt" in user_message["content"]

    projection_trace = payload["metadata"]["efp_projection"]
    assert projection_trace["endpoint"] == "chat_completions"
    assert projection_trace["messages"][0]["role"] == "system"
    assert projection_trace["messages"][0]["parts"][0]["context"]["type"] == "compaction_summary"
    assert projection_trace["messages"][1]["parts"][2]["attachment"]["filename"] == "notes.txt"


def test_tool_schema_projects_to_openai_chat_tool_shape():
    schema = RequestToolSchema(
        id="search",
        name="search",
        description="Search indexed project notes.",
        json_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        metadata={"category": "retrieval"},
    )

    assert request_tool_schema_to_openai_tool(schema) == {
        "type": "function",
        "function": {
            "name": "search",
            "description": "Search indexed project notes.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    }

    payload = provider_request_to_openai_chat(
        ProviderRequest(messages=[], tools=[schema]),
        model="gpt-test",
    )
    assert payload["tools"][0]["function"]["name"] == "search"
    assert payload["metadata"]["efp_projection"]["tools"][0]["metadata"] == {
        "category": "retrieval"
    }


def test_chat_projection_projects_assistant_tool_calls():
    tool_call = RequestToolCall(
        call_id="call-1",
        tool_name="search",
        arguments={"query": "EFP runtime"},
        arguments_text='{"query":"EFP runtime"}',
        metadata={"origin": "assistant"},
    )
    request = ProviderRequest(
        messages=[
            RequestMessage(
                role="assistant",
                parts=[
                    RequestMessagePart(type="text", text="I will search."),
                    RequestMessagePart(type="tool_call", tool_call=tool_call),
                ],
            )
        ]
    )

    payload = provider_request_to_openai_chat(request, model="gpt-test")

    message = payload["messages"][0]
    assert message["role"] == "assistant"
    assert message["content"] == "I will search."
    assert message["tool_calls"] == [
        {
            "id": "call-1",
            "type": "function",
            "function": {
                "name": "search",
                "arguments": '{"query":"EFP runtime"}',
            },
        }
    ]


def test_chat_projection_projects_tool_results_as_tool_messages():
    request = ProviderRequest(
        messages=[
            RequestMessage(
                role="tool",
                parts=[
                    RequestMessagePart(
                        type="tool_result",
                        tool_result=RequestToolResult(
                            call_id="call-1",
                            tool_name="search",
                            content="found EFP runtime notes",
                            output={"matches": 1},
                        ),
                    )
                ],
            )
        ]
    )

    payload = provider_request_to_openai_chat(request, model="gpt-test")

    assert payload["messages"] == [
        {
            "role": "tool",
            "tool_call_id": "call-1",
            "name": "search",
            "content": "found EFP runtime notes",
        }
    ]


def test_responses_projection_preserves_typed_input_items():
    request = ProviderRequest(
        messages=[
            RequestMessage(
                role="user",
                parts=[
                    RequestMessagePart(type="text", text="Use the project notes."),
                    RequestMessagePart(
                        type="context",
                        context=RequestContext(type="task", text="Find EFP runtime details."),
                    ),
                ],
            ),
            RequestMessage(
                role="assistant",
                parts=[
                    RequestMessagePart(
                        type="tool_call",
                        tool_call=RequestToolCall(
                            call_id="call-1",
                            tool_name="search",
                            arguments={"query": "EFP runtime"},
                        ),
                    )
                ],
            ),
            RequestMessage(
                role="tool",
                parts=[
                    RequestMessagePart(
                        type="tool_result",
                        tool_result=RequestToolResult(
                            call_id="call-1",
                            tool_name="search",
                            content="found EFP runtime notes",
                            success=True,
                        ),
                    )
                ],
            ),
        ]
    )

    payload = provider_request_to_openai_responses(
        request,
        model="gpt-test",
        instructions="Be concise.",
        stream=True,
    )

    assert payload["model"] == "gpt-test"
    assert payload["instructions"] == "Be concise."
    assert payload["stream"] is True
    assert [message["role"] for message in payload["input"]] == ["user", "assistant", "tool"]
    assert [item["type"] for item in payload["input"][0]["content"]] == [
        "input_text",
        "input_text",
    ]
    assert payload["input"][0]["content"][1]["text"].startswith("[context:task]")

    tool_call = payload["input"][1]["content"][0]
    assert tool_call["type"] == "function_call"
    assert tool_call["call_id"] == "call-1"
    assert tool_call["name"] == "search"
    assert tool_call["arguments"] == '{"query":"EFP runtime"}'
    assert tool_call["arguments_json"] == {"query": "EFP runtime"}

    tool_result = payload["input"][2]["content"][0]
    assert tool_result["type"] == "function_call_output"
    assert tool_result["call_id"] == "call-1"
    assert tool_result["output"] == "found EFP runtime notes"


def test_responses_projection_uses_role_aware_text_content_types():
    request = ProviderRequest(
        messages=[
            RequestMessage(
                role="user",
                parts=[RequestMessagePart(type="text", text="Use project notes.")],
            ),
            RequestMessage(
                role="assistant",
                parts=[
                    RequestMessagePart(type="text", text="Checking the index."),
                    RequestMessagePart(
                        type="context",
                        context=RequestContext(type="task", text="Summarize results."),
                    ),
                    RequestMessagePart(
                        type="attachment",
                        attachment=RequestAttachment(
                            attachment_id="att-1",
                            mime_type="text/plain",
                            filename="notes.txt",
                        ),
                    ),
                ],
            ),
            RequestMessage(role="assistant", parts=[]),
            RequestMessage(role="developer", parts=[]),
        ]
    )

    payload = provider_request_to_openai_responses(request, model="gpt-test")

    assert payload["input"][0]["content"][0]["type"] == "input_text"
    assert [item["type"] for item in payload["input"][1]["content"]] == [
        "output_text",
        "output_text",
        "output_text",
    ]
    assert payload["input"][2]["content"] == [{"type": "output_text", "text": ""}]
    assert payload["input"][3]["content"] == [{"type": "input_text", "text": ""}]


def test_responses_projection_shortens_long_tool_call_ids_without_breaking_pairs():
    long_call_id = "call_" + ("copilot_raw_tool_call_id_" * 18)
    tool_call = RequestToolCall(
        call_id=long_call_id,
        tool_name="search",
        arguments={"query": "runtime"},
    )
    tool_result = RequestToolResult(
        call_id=long_call_id,
        tool_name="search",
        content="found runtime notes",
    )
    request = ProviderRequest(
        messages=[
            RequestMessage(
                role="assistant",
                parts=[RequestMessagePart(type="tool_call", tool_call=tool_call)],
            ),
            RequestMessage(
                role="tool",
                parts=[RequestMessagePart(type="tool_result", tool_result=tool_result)],
            ),
        ]
    )

    first_payload = provider_request_to_openai_responses(request, model="gpt-test")
    second_payload = provider_request_to_openai_responses(request, model="gpt-test")

    projected_call = first_payload["input"][0]["content"][0]
    projected_result = first_payload["input"][1]["content"][0]
    projected_call_id = projected_call["call_id"]

    assert len(long_call_id) > 64
    assert projected_call["type"] == "function_call"
    assert projected_result["type"] == "function_call_output"
    assert projected_call_id == projected_result["call_id"]
    assert projected_call_id == second_payload["input"][0]["content"][0]["call_id"]
    assert projected_call_id.startswith("call_")
    assert len(projected_call_id) <= 64
    assert projected_call_id != long_call_id
    assert projected_call["call_id"] != long_call_id
    assert projected_result["call_id"] != long_call_id
    assert tool_call.call_id == long_call_id
    assert tool_result.call_id == long_call_id


def test_openai_projection_imports_standalone_with_pythonpath_src():
    code = """
import json
import sys

import efp_runtime.llm.openai

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
