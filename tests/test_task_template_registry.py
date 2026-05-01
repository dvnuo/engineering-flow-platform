from src.runtime.task_template_registry import get_task_template, resolve_task_template_from_payload


def test_github_comment_mention_template_present_and_resolvable():
    template = get_task_template("github_comment_mention")
    assert template is not None
    assert template.task_type == "triggered_event_task"
    assert template.default_trigger == "github_comment_mention"
    assert template.default_skill_name == "handle-triggered-event"

    resolved = resolve_task_template_from_payload({"task_template_id": "github_comment_mention"})
    assert resolved is not None
    assert resolved.task_type == "triggered_event_task"


def test_github_comment_mention_template_includes_commit_fields():
    template = get_task_template("github_comment_mention")
    assert template is not None
    assert "commit_id" in template.optional_inputs
    assert "commit_sha" in template.optional_inputs
    assert "position" in template.optional_inputs


def test_github_comment_mention_template_includes_discussion_fields():
    template = get_task_template("github_comment_mention")
    assert template is not None
    assert "discussion_number" in template.optional_inputs
    assert "discussion_id" in template.optional_inputs
    assert "discussion_comment_id" in template.optional_inputs
    assert "reply_to_id" in template.optional_inputs


def test_github_comment_mention_template_includes_notification_metadata():
    template = get_task_template("github_comment_mention")
    assert template is not None
    assert "notification_id" in template.optional_inputs
    assert "notification_reason" in template.optional_inputs
    assert "notification_subject_type" in template.optional_inputs
    assert "notification_url" in template.optional_inputs
    assert "notification_updated_at" in template.optional_inputs
