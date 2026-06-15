"""Question broker primitives for EFP runtime interactive tool pauses."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
import hashlib
import json
from typing import Any, Optional, Tuple, Union

from .types import utc_now_iso


AnswerValue = Union[str, Iterable[str]]


@dataclass
class QuestionOption:
    label: str
    description: str = ""

    def __post_init__(self) -> None:
        self.label = str(self.label)
        self.description = str(self.description or "")

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "description": self.description,
        }


@dataclass
class QuestionPrompt:
    question: str
    header: str = ""
    options: list[QuestionOption] = field(default_factory=list)
    custom: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.question = str(self.question)
        self.header = str(self.header or "")
        self.options = [_normalize_option(option) for option in self.options]
        self.custom = bool(self.custom)
        self.metadata = dict(self.metadata or {})

    def to_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "header": self.header,
            "options": [option.to_dict() for option in self.options],
            "custom": self.custom,
            "metadata": _json_safe(dict(self.metadata)),
        }


@dataclass
class QuestionRequest:
    request_id: str
    session_id: Optional[str]
    tool_call_id: Optional[str]
    questions: list[QuestionPrompt]
    created_at: str
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def id(self) -> str:
        return self.request_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "id": self.request_id,
            "session_id": self.session_id,
            "tool_call_id": self.tool_call_id,
            "questions": [question.to_dict() for question in self.questions],
            "created_at": self.created_at,
            "metadata": _json_safe(dict(self.metadata)),
        }


class QuestionBroker:
    """Stateful broker for model-initiated questions waiting on user input."""

    def __init__(self) -> None:
        self._pending: dict[str, QuestionRequest] = {}
        self._answers: dict[Tuple[Optional[str], Optional[str]], list[list[str]]] = {}

    def ask(
        self,
        session_id: Optional[str],
        tool_call_id: Optional[str],
        questions: Iterable[QuestionPrompt],
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> QuestionRequest:
        resolved_questions = [_normalize_prompt(question) for question in questions]
        request_metadata = dict(metadata or {})
        request_id = _make_request_id(
            session_id=session_id,
            tool_call_id=tool_call_id,
            questions=resolved_questions,
            metadata=request_metadata,
        )
        existing = self._pending.get(request_id)
        if existing is not None:
            return existing

        request = QuestionRequest(
            request_id=request_id,
            session_id=session_id,
            tool_call_id=tool_call_id,
            questions=resolved_questions,
            created_at=utc_now_iso(),
            metadata=request_metadata,
        )
        self._pending[request_id] = request
        return request

    def pending(self) -> list[QuestionRequest]:
        return list(self._pending.values())

    def get(self, request_id: str) -> Optional[QuestionRequest]:
        return self._pending.get(request_id)

    def answer(self, request_id: str, answers: Iterable[AnswerValue]) -> None:
        request = self._pending.pop(request_id, None)
        if request is None:
            raise KeyError(f"Unknown question request: {request_id}")
        self.seed_answer(request.session_id, request.tool_call_id, answers)

    def seed_answer(
        self,
        session_id: Optional[str],
        tool_call_id: Optional[str],
        answers: Iterable[AnswerValue],
    ) -> None:
        answer_items: Iterable[AnswerValue]
        if isinstance(answers, str):
            answer_items = [answers]
        else:
            answer_items = answers
        normalized_answers = [_normalize_answer(answer) for answer in answer_items]
        self._answers[(session_id, tool_call_id)] = normalized_answers

    def consume_answer(
        self,
        session_id: Optional[str],
        tool_call_id: Optional[str],
    ) -> Optional[list[list[str]]]:
        return self._answers.pop((session_id, tool_call_id), None)


def _normalize_option(option: Union[QuestionOption, Mapping[str, Any]]) -> QuestionOption:
    if isinstance(option, QuestionOption):
        return option
    if not isinstance(option, Mapping):
        raise TypeError("Question option must be a mapping or QuestionOption.")
    return QuestionOption(
        label=str(option.get("label", "")),
        description=str(option.get("description", "") or ""),
    )


def _normalize_prompt(prompt: Union[QuestionPrompt, Mapping[str, Any]]) -> QuestionPrompt:
    if isinstance(prompt, QuestionPrompt):
        return prompt
    if not isinstance(prompt, Mapping):
        raise TypeError("Question prompt must be a mapping or QuestionPrompt.")
    return QuestionPrompt(
        question=str(prompt.get("question", "")),
        header=str(prompt.get("header", "") or ""),
        options=[_normalize_option(option) for option in prompt.get("options", []) or []],
        custom=bool(prompt.get("custom", True)),
        metadata=dict(prompt.get("metadata", {}) or {}),
    )


def _normalize_answer(answer: AnswerValue) -> list[str]:
    if isinstance(answer, str):
        return [answer]
    return [str(item) for item in answer]


def _make_request_id(
    *,
    session_id: Optional[str],
    tool_call_id: Optional[str],
    questions: Iterable[QuestionPrompt],
    metadata: Mapping[str, Any],
) -> str:
    payload = {
        "session_id": session_id,
        "tool_call_id": tool_call_id,
        "questions": [question.to_dict() for question in questions],
        "metadata": dict(metadata),
    }
    digest = hashlib.sha256(_stable_json(payload).encode("utf-8")).hexdigest()
    return f"question_{digest[:24]}"


def _stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, sort_keys=True, default=str))


__all__ = [
    "QuestionBroker",
    "QuestionOption",
    "QuestionPrompt",
    "QuestionRequest",
]
