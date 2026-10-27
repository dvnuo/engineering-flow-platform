"""The final chat payload tells the Portal when a run stopped at its credits guard or step cap."""

from __future__ import annotations

from src.efp_runtime.events import RuntimeEvent
from src.gateway.chat_payloads import build_runtime_response_payload


def test_budget_pause_from_run_budget_paused_event_dicts():
    payload = build_runtime_response_payload(
        {
            "response": "Summary so far.",
            "usage": {"input_tokens": 10, "output_tokens": 2, "estimated_credits": 210.5},
            "runtime_events": [
                {"type": "iteration_start", "payload": {"iteration": 1}},
                {
                    "type": "run_budget_paused",
                    "message": "Estimated credits for this message reached the limit; reply to continue.",
                    "payload": {
                        "run_id": "run-1",
                        "reason": "credits_guard",
                        "estimated_credits": 210.5,
                        "credits_limit": 200.0,
                        "iterations": 9,
                        "iteration_limit": 30,
                        "tool_call_count": 0,
                    },
                },
                {"type": "run_finish", "payload": {"status": "completed"}},
            ],
        },
        "session-1",
    )

    assert payload["terminal_reason"] == "budget_paused"
    assert payload["budget_pause"] == {
        "reason": "credits_guard",
        "estimated_credits": 210.5,
        "credits_limit": 200.0,
        "iterations": 9,
        "iteration_limit": 30,
        "message": "Estimated credits for this message reached the limit; reply to continue.",
    }
    assert payload["usage"]["estimated_credits"] == 210.5


def test_iteration_cap_pause_keeps_the_max_steps_terminal_reason():
    events = [
        RuntimeEvent(
            type="run_budget_paused",
            message="Step limit reached for this message; reply to continue.",
            session_id="session-2",
            payload={"reason": "iteration_cap", "iterations": 30, "iteration_limit": 30},
        )
    ]
    payload = build_runtime_response_payload({"response": "Done.", "runtime_events": events}, "session-2")

    assert payload["terminal_reason"] == "max_steps_reached"
    assert payload["budget_pause"]["reason"] == "iteration_cap"
    assert payload["budget_pause"]["iteration_limit"] == 30


def test_runs_without_a_pause_carry_no_budget_fields():
    payload = build_runtime_response_payload(
        {"response": "Done.", "runtime_events": [{"type": "run_finish", "payload": {}}]},
        "session-3",
    )

    assert "budget_pause" not in payload
    assert "terminal_reason" not in payload
