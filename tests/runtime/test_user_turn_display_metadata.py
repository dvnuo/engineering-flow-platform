"""The user turn keeps the member's words and files when the model saw an expanded prompt."""

from __future__ import annotations

from src.efp_runtime.loop.runner import _user_message_metadata
from src.gateway import runtime_chat


def test_run_metadata_carries_display_fields_only_when_present():
    base = dict(
        request_path="/api/chat/stream",
        request_id="req-1",
        user_name="alice",
        portal_user_id="7",
        portal_user_name="Alice",
        attached_images=None,
        reasoning_replay=None,
        execution_metadata={"runtime_profile_id": "rp-1"},
        agent_id="agent-1",
        agent_name="Agent",
        model="gpt-5.4",
    )

    expanded = runtime_chat._run_metadata(
        **base,
        attachments=["f1"],
        transient_model_message="Based on the following context...",
        original_user_message="can you help check ths logs?",
        display_attachments=[{"file_id": "f1", "name": "app.log", "type": "file"}],
    )
    assert expanded["original_user_message"] == "can you help check ths logs?"
    assert expanded["display_attachments"] == [{"file_id": "f1", "name": "app.log", "type": "file"}]
    assert expanded["internal_model_content_hidden"] is True
    assert expanded["attachments"] == ["f1"]
    assert expanded["runtime_profile_id"] == "rp-1"

    plain = runtime_chat._run_metadata(
        **base,
        attachments=None,
        transient_model_message=None,
        original_user_message="hello",
        display_attachments=None,
    )
    assert plain["original_user_message"] == "hello"
    assert "display_attachments" not in plain
    assert "internal_model_content_hidden" not in plain


def test_display_user_message_hides_placeholders():
    assert runtime_chat._display_user_message("  what is this?  ") == "what is this?"
    assert runtime_chat._display_user_message("[attachment]") == ""
    assert runtime_chat._display_user_message("[image]") == ""
    assert runtime_chat._display_user_message(None) == ""


def test_user_message_metadata_persists_display_fields():
    metadata = _user_message_metadata(
        {
            "portal_user_id": "7",
            "portal_user_name": "Alice",
            "original_user_message": "can you help check ths logs?",
            "display_attachments": [
                {"file_id": "f1", "name": "app.log", "type": "file"},
                "not-a-mapping",
            ],
            "internal_model_content_hidden": True,
        }
    )

    assert metadata["author_id"] == "7"
    assert metadata["author_name"] == "Alice"
    assert metadata["original_user_message"] == "can you help check ths logs?"
    assert metadata["display_attachments"] == [{"file_id": "f1", "name": "app.log", "type": "file"}]
    assert metadata["internal_model_content_hidden"] is True


def test_user_message_metadata_stays_minimal_without_display_fields():
    metadata = _user_message_metadata({"user_name": "bob", "display_attachments": [], "internal_model_content_hidden": False})

    assert metadata == {
        "source": "loop.user",
        "author_type": "human",
        "author_source": "runtime",
        "author_name": "bob",
    }
