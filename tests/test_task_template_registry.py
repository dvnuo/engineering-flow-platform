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
