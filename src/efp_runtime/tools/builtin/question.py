"""Built-in question tool for EFP runtime interactive pauses."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Optional

from ...events import RuntimeEvent
from ...input_sources import clean_params, clean_source
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
        description=(
            "Ask the user to decide something, and pause the run until they answer.\n"
            "\n"
            "Reach for this when the next step turns on a choice that is genuinely "
            "the user's to make: which of several viable approaches to take, which "
            "environment or account to act against, or which reading of an ambiguous "
            "request is the right one. Each option is rendered as a labelled choice "
            "the user clicks, so give every option a short `label` and a `description` "
            "saying what picking it leads to. A free-text box stays available beside "
            "the options unless you set `custom` to false.\n"
            "\n"
            "When the answer is an item the user would otherwise have to look up and "
            "type in -- a Jira issue, a pull request, a branch, a Confluence page -- "
            "set `source` on that question and the user picks the item from a list "
            "instead. The sources are `jira.issues` (`params` can narrow it by issue "
            'type, for example `{"types": ["Epic"]}`), `jira.projects`, '
            "`github.pull_requests`, `github.branches`, `github.repos` and "
            "`confluence.pages`; any other value is ignored and the question is asked "
            "as plain text. The answer reaches you the same way as every other answer: "
            "a string, here the identifier of the picked item, such as an issue key or "
            "a pull request URL.\n"
            "\n"
            "Do not use it for anything you can settle yourself -- a question the code "
            "already answers, or a choice with a conventional default. Ask once, "
            "carrying every open question in a single call, rather than stopping the "
            "run several times in a row."
        ),
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
                            "source": {
                                "type": "string",
                                "description": (
                                    "The kind of item the answer is, one of the sources "
                                    "named above, so the user picks it from a list."
                                ),
                            },
                            "params": {
                                "type": "object",
                                "description": (
                                    "Filters for `source`, for example "
                                    '{"types": ["Epic"]} with `jira.issues`.'
                                ),
                            },
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
        metadata=_question_metadata(raw_question),
    )


def _question_metadata(raw_question: Mapping[str, Any]) -> dict[str, Any]:
    """The question's metadata, carrying its picker hint when it has a valid one.

    The Portal reads ``source`` and ``source_params`` from here to offer a
    picker in place of the text box. They travel in the metadata, which is
    already stored and replayed with the question, rather than in new fields
    every reader of a question payload would have to learn.

    The tool is the only writer of the two keys: whatever the caller's own
    metadata holds under those names is dropped, so what the Portal finds there
    has always been through the checks below. A source that fails them leaves
    the question exactly as it would have been asked without one, params
    included, since params say nothing without the source they narrow.
    """

    metadata = dict(raw_question.get("metadata", {}) or {})
    metadata.pop("source", None)
    metadata.pop("source_params", None)
    source = clean_source(raw_question.get("source"))
    if source:
        metadata["source"] = source
        params = clean_params(raw_question.get("params"))
        if params:
            metadata["source_params"] = params
    return metadata


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
