"""Project native runtime events into Portal session.next event payloads."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from typing import Any

from src.utils.redaction import redact_value, safe_preview


DEFAULT_ENGINE = "efp-native"
MAX_SHORT_ID_LENGTH = 64
MAX_PREVIEW_LENGTH = 500
FAILED_RUN_STATUSES = {"error", "failed", "cancelled"}
PENDING_RUN_STATUSES = {"waiting_for_permission", "waiting_for_question"}
FAILED_TOOL_STATUSES = {
    "error",
    "failed",
    "failure",
    "permission_denied",
    "denied",
    "disabled",
}


@dataclass
class RuntimeEventProjector:
    """Stateful projector for one runtime request stream."""

    request_id: str | None = None
    agent_id: str | None = None
    agent_name: str | None = None
    model: str | None = None
    engine: str = DEFAULT_ENGINE
    _text_started: set[str] = field(default_factory=set)
    _reasoning_started: set[str] = field(default_factory=set)
    _tool_input_started: set[str] = field(default_factory=set)

    def project(self, event: Any) -> list[dict[str, Any]]:
        raw_event = coerce_runtime_event_dict(event)
        if is_projected_runtime_event(raw_event):
            return [raw_event]

        raw_type = _event_type(raw_event)
        payload = _payload(raw_event)
        created_at = _created_at(raw_event, payload)
        outputs: list[dict[str, Any]] = []

        if raw_type == "run_start":
            outputs.append(
                self._build(
                    raw_event,
                    payload,
                    "session.next.step.started",
                    state="running",
                    summary="Runtime step started",
                    created_at=created_at,
                    data={
                        "run_id": payload.get("run_id"),
                        "model": self.model,
                        "agent_id": self.agent_id,
                        "agent_name": self.agent_name,
                        "timestamp": created_at,
                        "sessionID": _session_id(raw_event, payload),
                        "max_iterations": payload.get("max_iterations"),
                        "enabled_tool_ids": _redacted(payload.get("enabled_tool_ids")),
                        "disabled_tool_ids": _redacted(payload.get("disabled_tool_ids")),
                    },
                )
            )
            return outputs

        if raw_type == "iteration_start":
            iteration = payload.get("iteration")
            outputs.append(
                self._build(
                    raw_event,
                    payload,
                    "runtime.iteration.started",
                    state="running",
                    summary=f"Iteration {iteration} started" if iteration is not None else "Iteration started",
                    created_at=created_at,
                    data={"run_id": payload.get("run_id"), "iteration": iteration},
                )
            )
            return outputs

        if raw_type == "llm.text_delta":
            part_id = _part_id(raw_event, payload) or _stable_part_id("text", raw_event, payload)
            if part_id not in self._text_started:
                self._text_started.add(part_id)
                outputs.append(
                    self._build(
                        raw_event,
                        payload,
                        "session.next.text.started",
                        state="running",
                        summary="Assistant text started",
                        created_at=created_at,
                        part_id=part_id,
                        data={
                            "message_id": _message_id(raw_event, payload),
                            "part_id": part_id,
                            "content": "",
                            "text": "",
                        },
                    )
                )
            delta = _text_delta(payload)
            outputs.append(
                self._build(
                    raw_event,
                    payload,
                    "session.next.text.delta",
                    state="running",
                    summary=safe_preview(delta, 160) if delta else "Assistant text delta",
                    created_at=created_at,
                    part_id=part_id,
                    data={
                        "message_id": _message_id(raw_event, payload),
                        "part_id": part_id,
                        "delta": delta,
                        "text_delta": delta,
                        "content_delta": delta,
                        "message_delta": delta,
                        "content": delta,
                        "text": delta,
                    },
                )
            )
            return outputs

        if raw_type == "llm.reasoning_start":
            reasoning_id = _reasoning_id(raw_event, payload)
            self._reasoning_started.add(reasoning_id)
            outputs.append(
                self._build(
                    raw_event,
                    payload,
                    "session.next.reasoning.started",
                    state="running",
                    summary="Reasoning started",
                    created_at=created_at,
                    data={"reasoning_id": reasoning_id, "reasoningID": reasoning_id},
                )
            )
            return outputs

        if raw_type == "llm.reasoning_delta":
            reasoning_id = _reasoning_id(raw_event, payload)
            if reasoning_id not in self._reasoning_started:
                self._reasoning_started.add(reasoning_id)
                outputs.append(
                    self._build(
                        raw_event,
                        payload,
                        "session.next.reasoning.started",
                        state="running",
                        summary="Reasoning started",
                        created_at=created_at,
                        data={"reasoning_id": reasoning_id, "reasoningID": reasoning_id},
                    )
                )
            delta = _text_delta(payload)
            outputs.append(
                self._build(
                    raw_event,
                    payload,
                    "session.next.reasoning.delta",
                    state="running",
                    summary=safe_preview(delta, 160) if delta else "Reasoning delta",
                    created_at=created_at,
                    data={
                        "reasoning_id": reasoning_id,
                        "reasoningID": reasoning_id,
                        "delta": delta,
                        "reasoning_delta": delta,
                        "text_delta": delta,
                        "content": delta,
                        "text": delta,
                    },
                )
            )
            return outputs

        if raw_type == "llm.reasoning_end":
            reasoning_id = _reasoning_id(raw_event, payload)
            self._reasoning_started.discard(reasoning_id)
            outputs.append(
                self._build(
                    raw_event,
                    payload,
                    "session.next.reasoning.ended",
                    state="success",
                    summary="Reasoning ended",
                    created_at=created_at,
                    data={"reasoning_id": reasoning_id, "reasoningID": reasoning_id},
                )
            )
            return outputs

        if raw_type == "llm.tool_call_delta":
            return self._project_llm_tool_delta(raw_event, payload, created_at)

        if raw_type == "llm.tool_call_done":
            tool_data = _tool_data(payload)
            outputs.append(
                self._build(
                    raw_event,
                    payload,
                    "session.next.tool.called",
                    state="running",
                    summary=_tool_summary("Tool call requested", tool_data),
                    created_at=created_at,
                    data=tool_data,
                )
            )
            return outputs

        if raw_type == "llm.tool_error":
            tool_data = _tool_data(payload)
            outputs.append(
                self._build(
                    raw_event,
                    payload,
                    "session.next.tool.failed",
                    state="failed",
                    summary=_tool_summary("Tool failed", tool_data),
                    created_at=created_at,
                    data={
                        **tool_data,
                        "status": "error",
                        "error": _redacted(payload.get("error") or payload.get("message")),
                    },
                )
            )
            return outputs

        if raw_type == "tool_call_start":
            tool_data = _tool_data(payload)
            outputs.append(
                self._build(
                    raw_event,
                    payload,
                    "session.next.tool.progress",
                    state="running",
                    summary=_tool_summary("Tool execution started", tool_data),
                    created_at=created_at,
                    data={**tool_data, "status": "running"},
                )
            )
            return outputs

        if raw_type == "tool_result_appended":
            tool_data = _tool_data(payload)
            status = str(payload.get("status") or "").strip().lower()
            failed = status in FAILED_TOOL_STATUSES or bool(payload.get("error"))
            target_type = "session.next.tool.failed" if failed else "session.next.tool.success"
            outputs.append(
                self._build(
                    raw_event,
                    payload,
                    target_type,
                    state="failed" if failed else "success",
                    summary=_tool_summary("Tool failed" if failed else "Tool completed", tool_data),
                    created_at=created_at,
                    data={
                        **tool_data,
                        "status": payload.get("status"),
                        "success": not failed,
                        "output_preview": _first_preview(
                            payload,
                            "output",
                            "content",
                            "result",
                            "error",
                        ),
                        "content_preview": _first_preview(payload, "content", "output", "result", "error"),
                        "error": _redacted(payload.get("error")),
                    },
                )
            )
            return outputs

        if raw_type == "tool.permission_requested":
            request_payload = _mapping_or_empty(payload.get("permission_request"))
            data = {
                **_tool_data(payload),
                "permission_request": _redacted(request_payload),
                "permissionRequest": _redacted(request_payload),
                "permission_request_id": request_payload.get("id") or request_payload.get("request_id"),
                "action": request_payload.get("action"),
                "risk": request_payload.get("risk"),
                "reason": request_payload.get("reason") or raw_event.get("message"),
            }
            event_payload = self._build(
                raw_event,
                payload,
                "permission.requested",
                state="pending",
                summary="Permission requested",
                created_at=created_at,
                data=data,
            )
            event_payload["permission_request"] = data["permission_request"]
            outputs.append(event_payload)
            return outputs

        if raw_type == "tool.question_requested":
            question_payload = _mapping_or_empty(payload.get("question_request"))
            outputs.append(
                self._build(
                    raw_event,
                    payload,
                    "question.requested",
                    state="pending",
                    summary="Question requested",
                    created_at=created_at,
                    data={
                        **_tool_data(payload),
                        "question_request": _redacted(question_payload),
                        "questionRequest": _redacted(question_payload),
                        "question_request_id": question_payload.get("id") or question_payload.get("request_id"),
                        "question": question_payload.get("question") or question_payload.get("prompt") or raw_event.get("message"),
                    },
                )
            )
            return outputs

        if raw_type in {"session_compaction_started", "provider.context_overflow_retry"}:
            outputs.append(
                self._build(
                    raw_event,
                    payload,
                    "session.next.compaction.started",
                    state="running",
                    summary=raw_event.get("message") or "Compaction started",
                    created_at=created_at,
                    data=_compaction_data(payload),
                )
            )
            return outputs

        if raw_type in {
            "session_compacted",
            "session_compaction_completed",
            "overflow_retry.compaction_applied",
        }:
            outputs.append(
                self._build(
                    raw_event,
                    payload,
                    "session.next.compaction.ended",
                    state="success",
                    summary=raw_event.get("message") or "Compaction completed",
                    created_at=created_at,
                    data=_compaction_data(payload),
                )
            )
            return outputs

        if raw_type == "usage.updated":
            outputs.append(
                self._build(
                    raw_event,
                    payload,
                    "usage.updated",
                    state="running",
                    summary="Usage updated",
                    created_at=created_at,
                    data={
                        "run_id": payload.get("run_id"),
                        "iteration": payload.get("iteration"),
                        "usage": _redacted(payload.get("usage")),
                        "step_usage": _redacted(payload.get("step_usage")),
                        "iteration_usage": _redacted(payload.get("iteration_usage")),
                        "pricing_enabled": payload.get("pricing_enabled"),
                    },
                )
            )
            return outputs

        if raw_type == "run_finish":
            status = str(payload.get("status") or "").strip().lower()
            failed = status in FAILED_RUN_STATUSES
            pending = status in PENDING_RUN_STATUSES
            outputs.extend(self._project_open_parts_ended(raw_event, payload, created_at))
            outputs.append(
                self._build(
                    raw_event,
                    payload,
                    "session.next.step.failed" if failed else "session.next.step.ended",
                    state="failed" if failed else "pending" if pending else "success",
                    summary=f"Runtime step {status or 'finished'}",
                    created_at=created_at,
                    data={
                        "run_id": payload.get("run_id"),
                        "status": payload.get("status"),
                        "iterations": payload.get("iterations"),
                        "usage": _redacted(payload.get("usage")),
                        "tokens": _tokens_from_usage(payload.get("usage")),
                        "cost": _cost_from_usage(payload.get("usage")),
                        "terminal_reason": payload.get("terminal_reason"),
                    },
                )
            )
            return outputs

        if raw_type in {"llm.step_finish", "llm.finish"}:
            outputs.extend(self._project_open_parts_ended(raw_event, payload, created_at))
            return outputs

        if raw_type in {"error", "llm.error", "llm.provider_error", "run_cancelled"}:
            outputs.extend(self._project_open_parts_ended(raw_event, payload, created_at))
            outputs.append(
                self._build(
                    raw_event,
                    payload,
                    "session.next.step.failed",
                    state="failed",
                    summary=raw_event.get("message") or safe_preview(payload.get("error"), 160) or "Runtime step failed",
                    created_at=created_at,
                    data={
                        "run_id": payload.get("run_id"),
                        "iteration": payload.get("iteration"),
                        "phase": payload.get("phase"),
                        "error": _redacted(payload.get("error") or raw_event.get("message")),
                        "error_type": payload.get("error_type"),
                        "code": payload.get("code"),
                        "retryable": payload.get("retryable"),
                    },
                )
            )
            return outputs

        outputs.append(
            self._build(
                raw_event,
                payload,
                "runtime.event",
                state=_state_from_payload(payload),
                summary=raw_event.get("message") or raw_type,
                created_at=created_at,
                data={
                    "run_id": payload.get("run_id"),
                    "iteration": payload.get("iteration"),
                    "payload_preview": safe_preview(payload, MAX_PREVIEW_LENGTH),
                },
            )
        )
        return outputs

    def _project_llm_tool_delta(
        self,
        raw_event: Mapping[str, Any],
        payload: Mapping[str, Any],
        created_at: str,
    ) -> list[dict[str, Any]]:
        llm_event_type = str(payload.get("llm_event_type") or "").strip()
        call_id = _short_call_id(payload.get("tool_call_id") or payload.get("call_id"))
        outputs: list[dict[str, Any]] = []
        tool_data = _tool_data(payload)
        tool_data["llm_event_type"] = llm_event_type

        if llm_event_type == "tool_input_start":
            if call_id:
                self._tool_input_started.add(call_id)
            outputs.append(
                self._build(
                    raw_event,
                    payload,
                    "session.next.tool.input.started",
                    state="running",
                    summary=_tool_summary("Tool input started", tool_data),
                    created_at=created_at,
                    data=tool_data,
                )
            )
            return outputs

        if call_id and call_id not in self._tool_input_started:
            self._tool_input_started.add(call_id)
            outputs.append(
                self._build(
                    raw_event,
                    payload,
                    "session.next.tool.input.started",
                    state="running",
                    summary=_tool_summary("Tool input started", tool_data),
                    created_at=created_at,
                    data=tool_data,
                )
            )

        if llm_event_type == "tool_input_delta":
            delta = str(payload.get("arguments_delta") or payload.get("delta") or payload.get("text") or "")
            outputs.append(
                self._build(
                    raw_event,
                    payload,
                    "session.next.tool.input.delta",
                    state="running",
                    summary=safe_preview(delta, 160) if delta else _tool_summary("Tool input delta", tool_data),
                    created_at=created_at,
                    data={
                        **tool_data,
                        "delta": delta,
                        "input_delta": delta,
                        "arguments_delta": delta,
                        "input_preview": safe_preview(delta, MAX_PREVIEW_LENGTH),
                        "arguments_preview": safe_preview(delta, MAX_PREVIEW_LENGTH),
                    },
                )
            )
            return outputs

        if llm_event_type == "tool_input_end":
            arguments = payload.get("arguments")
            outputs.append(
                self._build(
                    raw_event,
                    payload,
                    "session.next.tool.input.ended",
                    state="running",
                    summary=_tool_summary("Tool input ended", tool_data),
                    created_at=created_at,
                    data={
                        **tool_data,
                        "arguments": _redacted(arguments),
                        "input": _redacted(arguments),
                        "input_preview": safe_preview(arguments, MAX_PREVIEW_LENGTH),
                        "arguments_preview": safe_preview(arguments, MAX_PREVIEW_LENGTH),
                    },
                )
            )
            return outputs

        outputs.append(
            self._build(
                raw_event,
                payload,
                "session.next.tool.input.delta",
                state="running",
                summary=_tool_summary("Tool input event", tool_data),
                created_at=created_at,
                data=tool_data,
            )
        )
        return outputs

    def _project_open_parts_ended(
        self,
        raw_event: Mapping[str, Any],
        payload: Mapping[str, Any],
        created_at: str,
    ) -> list[dict[str, Any]]:
        outputs: list[dict[str, Any]] = []
        for part_id in sorted(self._text_started):
            outputs.append(
                self._build(
                    raw_event,
                    payload,
                    "session.next.text.ended",
                    state="success",
                    summary="Assistant text ended",
                    created_at=created_at,
                    part_id=part_id,
                    data={"part_id": part_id, "message_id": _message_id(raw_event, payload)},
                )
            )
        self._text_started.clear()
        for reasoning_id in sorted(self._reasoning_started):
            outputs.append(
                self._build(
                    raw_event,
                    payload,
                    "session.next.reasoning.ended",
                    state="success",
                    summary="Reasoning ended",
                    created_at=created_at,
                    data={"reasoning_id": reasoning_id, "reasoningID": reasoning_id},
                )
            )
        self._reasoning_started.clear()
        return outputs

    def _build(
        self,
        raw_event: Mapping[str, Any],
        payload: Mapping[str, Any],
        target_type: str,
        *,
        state: str,
        summary: Any,
        created_at: str,
        data: Mapping[str, Any] | None = None,
        part_id: str | None = None,
    ) -> dict[str, Any]:
        raw_type = _event_type(raw_event)
        session_id = _session_id(raw_event, payload)
        message_id = _message_id(raw_event, payload)
        resolved_part_id = part_id or _part_id(raw_event, payload)
        clean_data = {
            "type": target_type,
            "event_type": target_type,
            "engine": self.engine,
            "session_id": session_id,
            "request_id": self.request_id,
            "agent_id": self.agent_id,
            "agent_name": self.agent_name,
            "model": self.model,
            "message_id": message_id,
            "part_id": resolved_part_id,
            "raw_type": raw_type,
            "created_at": created_at,
            "state": state,
            "summary": safe_preview(summary, 240),
        }
        clean_data.update({key: value for key, value in dict(data or {}).items() if value is not None})
        clean_data = _redacted(clean_data)
        event_id = _event_id(
            target_type=target_type,
            raw_event=raw_event,
            payload=payload,
            request_id=self.request_id,
            session_id=session_id,
            part_id=resolved_part_id,
        )
        return {
            "id": event_id,
            "type": target_type,
            "event_type": target_type,
            "engine": self.engine,
            "session_id": session_id,
            "request_id": self.request_id,
            "agent_id": self.agent_id,
            "message_id": message_id,
            "part_id": resolved_part_id,
            "raw_type": raw_type,
            "created_at": created_at,
            "state": state,
            "summary": clean_data["summary"],
            "properties": dict(clean_data),
            "data": dict(clean_data),
        }


def project_runtime_event(
    event: Any,
    *,
    request_id: str | None = None,
    agent_id: str | None = None,
    agent_name: str | None = None,
    model: str | None = None,
    engine: str = DEFAULT_ENGINE,
) -> list[dict[str, Any]]:
    return RuntimeEventProjector(
        request_id=request_id,
        agent_id=agent_id,
        agent_name=agent_name,
        model=model,
        engine=engine,
    ).project(event)


def coerce_runtime_event_dict(event: Any) -> dict[str, Any]:
    if hasattr(event, "to_dict"):
        converted = event.to_dict()
        if isinstance(converted, Mapping):
            return dict(converted)
    if isinstance(event, Mapping):
        return dict(event)
    if isinstance(event, str):
        text = event.strip()
        if text.startswith("{") and text.endswith("}"):
            try:
                parsed = json.loads(text)
                if isinstance(parsed, Mapping):
                    return dict(parsed)
            except json.JSONDecodeError:
                pass
        return {"type": "runtime.message", "message": safe_preview(event, MAX_PREVIEW_LENGTH), "payload": {"text": event}}
    return {
        "type": "runtime.event",
        "message": safe_preview(event, MAX_PREVIEW_LENGTH),
        "payload": {"value_preview": safe_preview(event, MAX_PREVIEW_LENGTH)},
    }


def is_projected_runtime_event(event: Mapping[str, Any]) -> bool:
    event_type = event.get("event_type") or event.get("type")
    return (
        isinstance(event_type, str)
        and (
            event_type.startswith("session.next.")
            or event_type in {"permission.requested", "question.requested", "usage.updated", "runtime.iteration.started", "runtime.event"}
        )
        and isinstance(event.get("data"), Mapping)
        and isinstance(event.get("properties"), Mapping)
    )


def _event_type(event: Mapping[str, Any]) -> str:
    value = event.get("type") or event.get("event_type") or "runtime.event"
    return str(value)


def _payload(event: Mapping[str, Any]) -> dict[str, Any]:
    payload = event.get("payload")
    if isinstance(payload, Mapping):
        return dict(payload)
    data = event.get("data")
    if isinstance(data, Mapping):
        return dict(data)
    return {}


def _created_at(event: Mapping[str, Any], payload: Mapping[str, Any]) -> str:
    for value in (event.get("created_at"), payload.get("created_at"), payload.get("timestamp")):
        if value:
            return str(value)
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _session_id(event: Mapping[str, Any], payload: Mapping[str, Any]) -> str | None:
    return _optional_str(event.get("session_id") or payload.get("session_id") or payload.get("sessionID"))


def _message_id(event: Mapping[str, Any], payload: Mapping[str, Any]) -> str | None:
    return _optional_str(event.get("message_id") or payload.get("message_id"))


def _part_id(event: Mapping[str, Any], payload: Mapping[str, Any]) -> str | None:
    return _optional_str(event.get("part_id") or payload.get("part_id"))


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _text_delta(payload: Mapping[str, Any]) -> str:
    return str(payload.get("delta") or payload.get("text") or payload.get("content") or "")


def _stable_part_id(prefix: str, raw_event: Mapping[str, Any], payload: Mapping[str, Any]) -> str:
    digest = _digest(
        [
            prefix,
            _session_id(raw_event, payload),
            payload.get("run_id"),
            payload.get("iteration"),
            _message_id(raw_event, payload),
        ]
    )
    return f"{prefix}_{digest[:12]}"


def _reasoning_id(raw_event: Mapping[str, Any], payload: Mapping[str, Any]) -> str:
    part_id = _part_id(raw_event, payload)
    if part_id:
        return _short_identifier(part_id, prefix="reasoning")
    digest = _digest(
        [
            "reasoning",
            _session_id(raw_event, payload),
            payload.get("run_id"),
            payload.get("iteration"),
            _message_id(raw_event, payload),
        ]
    )
    return f"reasoning_{digest[:12]}"


def _tool_data(payload: Mapping[str, Any]) -> dict[str, Any]:
    raw_call_id = payload.get("tool_call_id") or payload.get("call_id")
    call_id = _short_call_id(raw_call_id)
    arguments = payload.get("arguments")
    if arguments is None and payload.get("arguments_text") is not None:
        arguments = payload.get("arguments_text")
    data = {
        "tool_call_id": call_id,
        "call_id": call_id,
        "tool_name": payload.get("tool_name") or payload.get("name") or payload.get("tool"),
        "arguments": _redacted(arguments),
        "input": _redacted(arguments),
        "arguments_preview": safe_preview(arguments, MAX_PREVIEW_LENGTH) if arguments is not None else None,
        "input_preview": safe_preview(arguments, MAX_PREVIEW_LENGTH) if arguments is not None else None,
        "iteration": payload.get("iteration"),
        "run_id": payload.get("run_id"),
    }
    if raw_call_id is not None and str(raw_call_id) != str(call_id):
        data["raw_call_id_hash"] = _digest([raw_call_id])[:16]
    return {key: value for key, value in data.items() if value is not None}


def _short_call_id(value: Any) -> str | None:
    if value is None:
        return None
    return _short_identifier(str(value), prefix="call")


def _short_identifier(value: str, *, prefix: str) -> str:
    text = str(value)
    if len(text) <= MAX_SHORT_ID_LENGTH:
        return text
    return f"{prefix}_{_digest([text])[:16]}"


def _first_preview(payload: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if value not in (None, ""):
            return safe_preview(value, MAX_PREVIEW_LENGTH)
    return None


def _tool_summary(default: str, data: Mapping[str, Any]) -> str:
    tool_name = data.get("tool_name")
    if tool_name:
        return f"{default}: {tool_name}"
    return default


def _compaction_data(payload: Mapping[str, Any]) -> dict[str, Any]:
    compaction = payload.get("compaction")
    return {
        "run_id": payload.get("run_id"),
        "iteration": payload.get("iteration"),
        "attempt": payload.get("attempt"),
        "trigger": payload.get("trigger") or payload.get("compaction_trigger"),
        "compaction": _redacted(compaction),
        "summary_preview": _first_preview(payload, "summary", "compaction_summary"),
        "stored_message_count": payload.get("stored_message_count"),
        "overflow": payload.get("overflow"),
    }


def _mapping_or_empty(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _tokens_from_usage(usage: Any) -> Any:
    if not isinstance(usage, Mapping):
        return None
    for key in ("total_tokens", "tokens"):
        if usage.get(key) is not None:
            return usage.get(key)
    input_tokens = usage.get("prompt_tokens") or usage.get("input_tokens") or 0
    output_tokens = usage.get("completion_tokens") or usage.get("output_tokens") or 0
    if input_tokens or output_tokens:
        return input_tokens + output_tokens
    return None


def _cost_from_usage(usage: Any) -> Any:
    if not isinstance(usage, Mapping):
        return None
    for key in ("estimated_cost", "cost", "total_cost"):
        if usage.get(key) is not None:
            return usage.get(key)
    return None


def _state_from_payload(payload: Mapping[str, Any]) -> str:
    status = str(payload.get("status") or payload.get("state") or "").strip().lower()
    if status in FAILED_RUN_STATUSES or status in FAILED_TOOL_STATUSES:
        return "failed"
    if status in PENDING_RUN_STATUSES or status == "pending":
        return "pending"
    if status in {"success", "completed", "complete"}:
        return "success"
    return "running"


def _event_id(
    *,
    target_type: str,
    raw_event: Mapping[str, Any],
    payload: Mapping[str, Any],
    request_id: str | None,
    session_id: str | None,
    part_id: str | None,
) -> str:
    source = {
        "target_type": target_type,
        "raw_type": _event_type(raw_event),
        "request_id": request_id,
        "session_id": session_id,
        "message_id": _message_id(raw_event, payload),
        "part_id": part_id,
        "tool_call_id": _short_call_id(payload.get("tool_call_id") or payload.get("call_id")),
        "run_id": payload.get("run_id"),
        "iteration": payload.get("iteration"),
        "llm_event_type": payload.get("llm_event_type"),
        "created_at": raw_event.get("created_at") or payload.get("created_at") or payload.get("timestamp"),
        "delta_hash": _digest([payload.get("delta"), payload.get("arguments_delta"), payload.get("text")])[:12],
        "status": payload.get("status"),
    }
    return f"efp_{_digest([source])[:16]}"


def _digest(parts: Any) -> str:
    try:
        text = json.dumps(parts, sort_keys=True, default=str, separators=(",", ":"))
    except (TypeError, ValueError):
        text = str(parts)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _redacted(value: Any) -> Any:
    return redact_value(value)
