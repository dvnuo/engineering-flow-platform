from src.context_blob_store import put_text, read_ref


def test_context_blob_store_put_and_read_roundtrip():
    ref = put_text("sess-a", "jira_issue", "MMGFX-1", "Issue", "hello world")
    assert ref.startswith("ctx://context/sess-a/jira_issue/")
    assert read_ref(ref, session_id="sess-a") == "hello world"


def test_context_blob_store_max_chars_does_not_change_stored_content():
    text = "x" * 12000
    ref = put_text("sess-b", "confluence_page", "123", "Page", text)
    truncated = read_ref(ref, session_id="sess-b", max_chars=1000)
    assert len(truncated) > 1000
    assert "output truncated" in truncated
    assert read_ref(ref, session_id="sess-b", max_chars=13000) == text


def test_context_blob_store_wrong_session_rejected():
    ref = put_text("sess-c", "assistant_output", "msg1", "Assistant", "secret")
    try:
        read_ref(ref, session_id="sess-other")
        assert False, "expected PermissionError"
    except PermissionError:
        assert True

