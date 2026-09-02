"""A question keeps its identity for as long as it goes unanswered.

The loop replays pending tool calls before anything else, so a question nobody
has answered is raised again on every subsequent run -- including a run started
by the member typing into the composer instead of the card. Each of those used
to mint a new id, because the request metadata carries `run_id`, fresh per run,
and an `iteration` that is present on the first raise and absent on every
replay.

Downstream that read as a new question each time: Portal rebuilt its answer
card and discarded whatever had been typed into it, and `pending-input` named an
id that no longer matched the card on screen.
"""
from efp_runtime.questions import QuestionBroker, QuestionPrompt


def _prompt(text: str = "Which project?") -> QuestionPrompt:
    return QuestionPrompt(question=text, options=[], custom=True)


def _ask(broker: QuestionBroker, **metadata) -> str:
    return broker.ask("session-1", "call-1", [_prompt()], metadata=metadata).request_id


def test_the_same_question_re_raised_by_a_later_run_keeps_its_id():
    # A fresh broker per run is the real shape: AgentRuntime builds one each
    # time, so nothing is remembered between them except the id itself.
    first = _ask(QuestionBroker(), run_id="run-a", iteration=2, tool_name="question")
    replayed = _ask(QuestionBroker(), run_id="run-b", tool_name="question")

    assert first == replayed


def test_a_different_question_on_the_same_call_is_a_different_request():
    broker = QuestionBroker()
    which = broker.ask("session-1", "call-1", [_prompt("Which project?")]).request_id
    what = broker.ask("session-1", "call-1", [_prompt("What issue type?")]).request_id

    assert which != what


def test_the_same_question_in_another_session_is_a_different_request():
    broker = QuestionBroker()
    here = broker.ask("session-1", "call-1", [_prompt()]).request_id
    there = broker.ask("session-2", "call-1", [_prompt()]).request_id

    assert here != there


def test_the_same_question_from_another_tool_call_is_a_different_request():
    # Asking the same thing twice in one turn is two questions, and answering
    # one must not close the other.
    broker = QuestionBroker()
    first = broker.ask("session-1", "call-1", [_prompt()]).request_id
    second = broker.ask("session-1", "call-2", [_prompt()]).request_id

    assert first != second


def test_metadata_still_reaches_the_request_even_though_it_is_not_identity():
    # It is what the card and the resume path read; only the id ignores it.
    request = QuestionBroker().ask(
        "session-1", "call-1", [_prompt()], metadata={"run_id": "run-a", "tool_name": "question"}
    )

    assert request.metadata["run_id"] == "run-a"
    assert request.to_dict()["metadata"]["tool_name"] == "question"


def test_an_answer_recorded_for_the_call_is_found_after_a_replay():
    # The whole point of a stable id: the answer given to the card on screen
    # resolves the question the next run replays.
    broker = QuestionBroker()
    request = broker.ask("session-1", "call-1", [_prompt()])
    broker.answer(request.request_id, [["EFP"]])

    assert broker.consume_answer("session-1", "call-1") == [["EFP"]]
