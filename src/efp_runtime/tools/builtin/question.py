"""Built-in question tool for EFP runtime interactive pauses."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Optional

from ...events import RuntimeEvent
from ...permissions import ALLOW, PermissionMetadata
from ...questions import QuestionBroker, QuestionOption, QuestionPrompt
from ...types import ToolResult
from ..definition import ToolContext, ToolDef


def create_question_tool(
    broker: Optional[QuestionBroker] = None,
    tool_id: str = "question",
) -> ToolDef:
    """Create a tool that lets the model ask the user questions mid-loop."""

    question_broker = broker or QuestionBroker()

    async def execute(args: dict[str, Any], context: ToolContext) -> ToolResult:
        questions = _parse_questions(args.get("questions") or [])
        answers = question_broker.consume_answer(
            context.session_id,
            context.tool_call_id,
        )
        question_payload = [question.to_dict() for question in questions]
        if answers is not None:
            return ToolResult(
                call_id=context.tool_call_id or "",
                tool_name=tool_id,
                status="success",
                success=True,
                content=_answer_content(questions, answers),
                output={
                    "answers": answers,
                    "questions": question_payload,
                },
                metadata={
                    "answers": answers,
                    "questions": question_payload,
                },
            )

        request = question_broker.ask(
            context.session_id,
            context.tool_call_id,
            questions,
            metadata=_question_request_metadata(args, context),
        )
        request_payload = request.to_dict()
        return ToolResult(
            call_id=context.tool_call_id or "",
            tool_name=tool_id,
            status="question_requested",
            success=False,
            content="Question requires user input.",
            metadata={"question_request": request_payload},
            events=[
                RuntimeEvent(
                    type="tool.question_requested",
                    message="Question requires user input.",
                    payload={"question_request": request_payload},
                )
            ],
        )

    return ToolDef(
        id=tool_id,
        description="Ask the user one or more questions and wait for their answers.",
        input_schema={
            "type": "object",
            "required": ["questions"],
            "properties": {
                "questions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["question"],
                        "properties": {
                            "question": {"type": "string"},
                            "header": {"type": "string"},
                            "custom": {"type": "boolean"},
                            "metadata": {"type": "object"},
                            "options": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "required": ["label"],
                                    "properties": {
                                        "label": {"type": "string"},
                                        "description": {"type": "string"},
                                    },
                                },
                            },
                        },
                    },
                },
                "metadata": {"type": "object"},
            },
            "additionalProperties": False,
        },
        execute=execute,
        permission=PermissionMetadata(
            action=ALLOW,
            category="question",
            resource=tool_id,
            risk="low",
        ),
        metadata={"category": "question"},
    )


def _parse_questions(raw_questions: Any) -> list[QuestionPrompt]:
    if not isinstance(raw_questions, list):
        raise ValueError("questions must be an array.")
    return [_parse_question(item) for item in raw_questions]


def _parse_question(raw_question: Any) -> QuestionPrompt:
    if not isinstance(raw_question, Mapping):
        raise ValueError("question entries must be objects.")
    return QuestionPrompt(
        question=str(raw_question.get("question", "")),
        header=str(raw_question.get("header", "") or ""),
        custom=bool(raw_question.get("custom", True)),
        options=[
            QuestionOption(
                label=str(option.get("label", "")),
                description=str(option.get("description", "") or ""),
            )
            for option in raw_question.get("options", []) or []
            if isinstance(option, Mapping)
        ],
        metadata=dict(raw_question.get("metadata", {}) or {}),
    )


def _question_request_metadata(
    args: Mapping[str, Any],
    context: ToolContext,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    caller_metadata = args.get("metadata")
    if isinstance(caller_metadata, Mapping):
        metadata.update(dict(caller_metadata))

    message_id = context.messageID
    call_id = context.callID
    core_metadata: dict[str, Any] = {
        "tool_name": context.tool_name,
        "tool_call_id": context.tool_call_id,
        "run_id": context.run_id,
    }
    if context.iteration is not None:
        core_metadata["iteration"] = context.iteration
    if message_id is not None and message_id != "":
        core_metadata["message_id"] = message_id
    if call_id is not None and call_id != "":
        core_metadata["tool"] = {"message_id": message_id, "call_id": call_id}

    metadata.update(core_metadata)
    return {key: value for key, value in metadata.items() if value is not None}


def _answer_content(
    questions: list[QuestionPrompt],
    answers: list[list[str]],
) -> str:
    pairs: list[str] = []
    for index, question in enumerate(questions):
        answer_values = answers[index] if index < len(answers) else []
        answer_text = ", ".join(answer_values) if answer_values else "Unanswered"
        pairs.append(f'"{question.question}"="{answer_text}"')
    if not pairs:
        pairs.append('"question"="Unanswered"')
    return (
        "User has answered your questions: "
        f"{'; '.join(pairs)}. You can now continue with the user's answers in mind."
    )


__all__ = ["create_question_tool"]
