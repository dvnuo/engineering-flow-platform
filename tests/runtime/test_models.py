from efp_runtime import (
    Attachment,
    CompactionPart,
    LLMEvent,
    LLMEventType,
    Message,
    MessagePart,
    MessagePartType,
    PermissionMetadata,
    PermissionRequest,
    RuntimeEvent,
    SkillPackage,
    TaskPart,
    ToolCall,
    ToolResult,
)


def test_message_part_factories_create_structured_parts(tmp_path):
    attachment = Attachment(mime_type="text/plain", filename="notes.txt", text_ref="blob:1")
    call = ToolCall(tool_name="read_file", arguments={"path": "README.md"}, call_id="call-1")
    result = ToolResult(call_id=call.call_id, tool_name=call.tool_name, output="contents")
    compaction = CompactionPart(summary="User asked for a EFP runtime foundation.", auto=True)
    task = TaskPart(prompt="Create EFP runtime contracts", task_id="task-1", status="running")

    parts = [
        MessagePart.text_part("hello"),
        MessagePart.reasoning_part("Need a structured history model."),
        MessagePart.tool_call_part(call),
        MessagePart.tool_result_part(result),
        MessagePart.compaction_part(compaction),
        MessagePart.task_part(task),
        MessagePart.attachment_part(attachment),
    ]

    assert [part.type for part in parts] == [
        MessagePartType.TEXT,
        MessagePartType.REASONING,
        MessagePartType.TOOL_CALL,
        MessagePartType.TOOL_RESULT,
        MessagePartType.COMPACTION,
        MessagePartType.TASK,
        MessagePartType.ATTACHMENT,
    ]
    assert parts[2].tool_call.call_id == parts[3].tool_result.call_id
    assert parts[4].compaction.summary.startswith("User asked")
    assert parts[5].task.task_id == "task-1"
    assert parts[6].attachment.filename == "notes.txt"

    skill_file = tmp_path / "SKILL.md"
    skill = SkillPackage(
        name="requirements",
        description="Requirements workflow",
        root=tmp_path,
        skill_file=skill_file,
        content="# Requirements",
    )
    assert skill.location == str(skill_file)


def test_tool_call_and_result_keep_canonical_fields_and_aliases():
    call = ToolCall(id="call-1", tool_id="search", args={"query": "efp"})
    result = ToolResult(call_id=call.id, tool_id=call.tool_id, status="success", content="ok")

    assert call.call_id == "call-1"
    assert call.id == "call-1"
    assert call.tool_name == "search"
    assert call.tool_id == "search"
    assert call.arguments == {"query": "efp"}
    assert call.args == {"query": "efp"}
    assert result.tool_name == "search"
    assert result.tool_id == "search"
    assert result.success is True
    assert result.to_dict()["content"] == "ok"


def test_runtime_event_and_supporting_contracts_are_lightweight_data():
    call = ToolCall(tool_name="skill", arguments={"name": "requirements"}, call_id="call-skill")
    permission = PermissionRequest.create(
        tool_id=call.tool_name,
        args=call.arguments,
        metadata=PermissionMetadata(action="ask", reason="Load skill context."),
    )

    llm_event = LLMEvent(
        type=LLMEventType.TOOL_CALL_COMPLETE,
        message_id="message-1",
        tool_call=call,
    )
    runtime_event = RuntimeEvent(
        type="permission.requested",
        session_id="session-1",
        payload={"request_id": permission.request_id, "tool": call.tool_name},
    )

    message = Message.from_text("user", "Build EFP runtime.", session_id="session-1")

    assert llm_event.tool_call.call_id == "call-skill"
    assert runtime_event.payload["tool"] == "skill"
    assert permission.tool_id == call.tool_name
    assert message.parts[0].text == "Build EFP runtime."
