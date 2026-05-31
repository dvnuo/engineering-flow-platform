from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from efp_runtime.event_bus import RuntimeEventBus
from efp_runtime.loop import LoopStatus, RuntimeLoopRunner, ScriptedLLMProvider
from efp_runtime.questions import QuestionBroker, QuestionOption, QuestionPrompt
from efp_runtime.runtime import AgentRuntime, RuntimeConfig
from efp_runtime.session.models import MessagePartType, MessageRole
from efp_runtime.session.store import InMemorySessionStore
from efp_runtime.tools.builtin import create_core_tool_registry, create_question_tool
from efp_runtime.tools.definition import ToolContext
from efp_runtime.tools.registry import ToolRegistry
from efp_runtime.tools.runtime import ToolRuntime
from efp_runtime.types import ToolCall


ROOT = Path(__file__).resolve().parents[2]


def _question_args(question: str = "Which language should I use?") -> dict[str, Any]:
    return {
        "questions": [
            {
                "question": question,
                "header": "Language",
                "custom": True,
                "options": [
                    {"label": "Python", "description": "Use Python."},
                    {"label": "TypeScript", "description": "Use TypeScript."},
                ],
            }
        ]
    }


def _question_call(
    call_id: str = "call_question",
    question: str = "Which language should I use?",
) -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {
            "name": "question",
            "arguments": json.dumps(_question_args(question)),
        },
    }


def test_question_broker_pending_answer_and_consume_payload():
    broker = QuestionBroker()
    request = broker.ask(
        "session-question",
        "call-question",
        [
            QuestionPrompt(
                question="Which language?",
                header="Language",
                options=[QuestionOption("Python", "Use Python.")],
                metadata={"source": Path("prompt.md")},
            ),
            QuestionPrompt(question="Any constraints?"),
        ],
        metadata={"run_id": "run-1", "path": Path("README.md")},
    )

    assert request.request_id.startswith("question_")
    assert request.id == request.request_id
    assert broker.pending() == [request]
    assert broker.get(request.request_id) is request

    payload = request.to_dict()
    assert payload["id"] == request.request_id
    assert payload["request_id"] == request.request_id
    assert payload["session_id"] == "session-question"
    assert payload["tool_call_id"] == "call-question"
    assert payload["questions"][0]["question"] == "Which language?"
    assert payload["questions"][0]["options"][0]["label"] == "Python"
    assert payload["questions"][0]["metadata"]["source"] == "prompt.md"
    assert payload["metadata"]["path"] == "README.md"
    json.loads(json.dumps(payload))

    duplicate = broker.ask(
        "session-question",
        "call-question",
        request.questions,
        metadata={"run_id": "run-1", "path": Path("README.md")},
    )
    assert duplicate is request
    assert len(broker.pending()) == 1

    broker.answer(request.request_id, ["TypeScript", ["No external services"]])

    assert broker.pending() == []
    assert broker.get(request.request_id) is None
    assert broker.consume_answer("session-question", "call-question") == [
        ["TypeScript"],
        ["No external services"],
    ]
    assert broker.consume_answer("session-question", "call-question") is None


@pytest.mark.asyncio
async def test_question_tool_requests_then_consumes_answer():
    broker = QuestionBroker()
    runtime = ToolRuntime(ToolRegistry([create_question_tool(broker)]))
    call = ToolCall(id="call_question", tool_id="question", args=_question_args())

    first = await runtime.execute(
        call,
        context=ToolContext(session_id="session-tool"),
    )

    assert first.status == "question_requested"
    assert first.success is False
    assert first.content == "Question requires user input."
    request = first.metadata["question_request"]
    assert request["request_id"].startswith("question_")
    assert request["session_id"] == "session-tool"
    assert request["tool_call_id"] == "call_question"
    assert broker.pending()[0].to_dict() == request
    assert any(event.type == "tool.question_requested" for event in first.events)

    broker.answer(request["request_id"], ["TypeScript"])
    second = await runtime.execute(
        call,
        context=ToolContext(session_id="session-tool"),
    )

    assert second.status == "success"
    assert second.success is True
    assert second.output["answers"] == [["TypeScript"]]
    assert second.output["questions"][0]["question"] == "Which language should I use?"
    assert second.metadata["answers"] == [["TypeScript"]]
    assert '"Which language should I use?"="TypeScript"' in second.content
    assert broker.pending() == []


@pytest.mark.asyncio
async def test_question_tool_request_metadata_includes_message_and_tool_linkage():
    broker = QuestionBroker()
    runtime = ToolRuntime(ToolRegistry([create_question_tool(broker)]))
    args = _question_args()
    args["metadata"] = {
        "caller": "kept",
        "message_id": "caller-message",
        "tool": {"message_id": "caller-message", "call_id": "caller-call"},
    }
    call = ToolCall(id="call_question_link", tool_id="question", args=args)
    context = ToolContext(
        session_id="session-tool-link",
        tool_call_id="call_question_link",
        message_id="message-assistant",
        run_id="run-link",
        iteration=4,
    )

    first = await runtime.execute(call, context=context)

    assert first.status == "question_requested"
    metadata = first.metadata["question_request"]["metadata"]
    assert metadata["caller"] == "kept"
    assert metadata["tool_name"] == "question"
    assert metadata["tool_call_id"] == "call_question_link"
    assert metadata["run_id"] == "run-link"
    assert metadata["iteration"] == 4
    assert metadata["message_id"] == "message-assistant"
    assert metadata["tool"] == {
        "message_id": "message-assistant",
        "call_id": "call_question_link",
    }
    assert broker.pending()[0].to_dict()["metadata"] == metadata

    broker.answer(first.metadata["question_request"]["request_id"], ["Python"])
    second = await runtime.execute(call, context=context)

    assert second.status == "success"
    assert second.output["answers"] == [["Python"]]
    assert broker.pending() == []


@pytest.mark.asyncio
async def test_loop_waits_for_question_without_appending_tool_result():
    broker = QuestionBroker()
    bus = RuntimeEventBus()
    store = InMemorySessionStore()
    provider = ScriptedLLMProvider([{"tool_calls": [_question_call()]}])
    runner = RuntimeLoopRunner(
        store=store,
        provider=provider,
        tool_runtime=ToolRuntime(ToolRegistry([create_question_tool(broker)])),
        event_bus=bus,
        max_iterations=3,
    )

    result = await runner.run(
        user_text="Ask me what you need.",
        session_id="session-loop-question",
    )

    assert result.status == LoopStatus.WAITING_FOR_QUESTION
    assert result.pending_permission_request is None
    assert result.pending_question_request is not None
    assert result.pending_question_request["tool_call_id"] == "call_question"

    history = store.read_history("session-loop-question")
    assert [message.role for message in history] == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
    ]
    assert history[1].parts[0].type is MessagePartType.TOOL_CALL
    assert history[1].parts[0].tool_call.call_id == "call_question"
    assert not any(
        part.type is MessagePartType.TOOL_RESULT
        for message in history
        for part in message.parts
    )

    question_events = [
        event
        for event in bus.history("session-loop-question")
        if event.type == "tool.question_requested"
    ]
    assert len(question_events) == 1
    assert question_events[0].payload["tool_call_id"] == "call_question"
    assert question_events[0].payload["tool_name"] == "question"
    assert question_events[0].payload["question_request"] == (
        result.pending_question_request
    )
    assert question_events[0].payload["run_id"]
    assert bus.history("session-loop-question")[-1].type == "run_finish"
    assert bus.history("session-loop-question")[-1].payload["status"] == (
        LoopStatus.WAITING_FOR_QUESTION
    )


@pytest.mark.asyncio
async def test_agent_answer_question_then_resume_appends_result_and_continues(tmp_path: Path):
    provider = ScriptedLLMProvider(
        [
            {"tool_calls": [_question_call(question="Which stack?")]},
            {"content": "I will use TypeScript."},
        ]
    )
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            workspace_root=tmp_path,
            max_iterations=3,
            enable_question_tool=True,
        ),
    )

    first = await runtime.run("Clarify before coding.", session_id="session-agent-question")

    assert first.status == LoopStatus.WAITING_FOR_QUESTION
    assert first.pending_question_request is not None
    assert first.pending_permission_request is None
    assert runtime.pending_permissions() == []
    pending_questions = runtime.pending_questions()
    assert pending_questions == [first.pending_question_request]

    runtime.answer_question(pending_questions[0]["request_id"], ["TypeScript"])
    resumed = await runtime.resume("session-agent-question")

    assert resumed.status == LoopStatus.COMPLETED
    assert len(provider.requests) == 2
    assert [message.role for message in provider.requests[1].messages] == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
        MessageRole.TOOL,
    ]

    history = runtime.store.read_history("session-agent-question")
    assert [message.role for message in history] == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
        MessageRole.TOOL,
        MessageRole.ASSISTANT,
    ]
    assert sum(1 for message in history if message.role is MessageRole.USER) == 1
    tool_result = history[2].parts[0].tool_result
    assert tool_result.call_id == "call_question"
    assert tool_result.status == "success"
    assert tool_result.metadata["answers"] == [["TypeScript"]]
    assert '"Which stack?"="TypeScript"' in tool_result.content
    assert history[3].parts[0].text == "I will use TypeScript."


def test_question_registry_is_opt_in(tmp_path: Path):
    default_registry = create_core_tool_registry(tmp_path)
    question_registry = create_core_tool_registry(
        tmp_path,
        include_question_tool=True,
        question_broker=QuestionBroker(),
    )
    runtime = AgentRuntime(
        provider=ScriptedLLMProvider([]),
        config=RuntimeConfig(workspace_root=tmp_path, enable_question_tool=True),
    )

    assert default_registry.get("question") is None
    assert question_registry.get("question") is not None
    assert runtime.tool_runtime.registry.get("question") is not None


@pytest.mark.asyncio
async def test_question_and_permission_pending_state_do_not_overlap(tmp_path: Path):
    provider = ScriptedLLMProvider([{"tool_calls": [_question_call()]}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            workspace_root=tmp_path,
            max_iterations=3,
            enable_question_tool=True,
        ),
    )

    result = await runtime.run("Ask a question.", session_id="session-no-permission")

    assert result.status == LoopStatus.WAITING_FOR_QUESTION
    assert runtime.pending_permissions() == []
    assert runtime.pending_questions() == [result.pending_question_request]


def test_question_tool_import_boundary():
    code = """
import json
import sys

from efp_runtime.questions import QuestionBroker, QuestionPrompt
from efp_runtime.tools.builtin import create_question_tool

broker = QuestionBroker()
request = broker.ask("session", "call", [QuestionPrompt(question="Q?")])
tool = create_question_tool(broker)
legacy_modules = [
    "src.sessions",
    "src.agents.core",
    "src.runtime",
    "src.skills",
]
print(json.dumps({
    "tool_id": tool.id,
    "request_prefix": request.request_id.split("_", 1)[0],
    "pending": [item.to_dict() for item in broker.pending()],
    "legacy_loaded": [name for name in legacy_modules if name in sys.modules],
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
    assert payload["tool_id"] == "question"
    assert payload["request_prefix"] == "question"
    assert payload["pending"][0]["questions"][0]["question"] == "Q?"
    assert payload["legacy_loaded"] == []


def test_question_sources_stay_inside_runtime_boundary():
    combined = "\n".join(
        [
            (ROOT / "src/efp_runtime/questions.py").read_text(encoding="utf-8"),
            (ROOT / "src/efp_runtime/tools/builtin/question.py").read_text(
                encoding="utf-8"
            ),
            (ROOT / "src/efp_runtime/loop/runner.py").read_text(encoding="utf-8"),
            (ROOT / "src/efp_runtime/runtime/agent.py").read_text(encoding="utf-8"),
        ]
    )
    forbidden_tokens = [
        "from src.efp_runtime",
        "import src.efp_runtime",
        "from src.sessions",
        "import src.sessions",
        "from src.agents.core",
        "import src.agents.core",
        "from src.runtime",
        "import src.runtime",
        "from src.skills",
        "import src.skills",
    ]
    for token in forbidden_tokens:
        assert token not in combined
