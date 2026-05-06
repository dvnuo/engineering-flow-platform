from pathlib import Path


def test_p3_contract_docs_exist_and_are_indexed():
    assert Path("docs/runtime_contract.md").exists()
    assert Path("docs/observability_contract.md").exists()
    docs_index = Path("docs/README.md").read_text(encoding="utf-8")
    assert "runtime_contract.md" in docs_index
    assert "observability_contract.md" in docs_index
    root_readme = Path("README.md").read_text(encoding="utf-8")
    assert "docs/runtime_contract.md" in root_readme
    assert "docs/observability_contract.md" in root_readme


def test_runtime_contract_doc_mentions_current_native_contract_surfaces():
    text = Path("docs/runtime_contract.md").read_text(encoding="utf-8")
    for needle in [
        "/api/chat",
        "/api/chat/stream",
        "/api/events",
        "/api/capabilities",
        "/api/skills",
        "/api/tasks/execute",
        "/api/tasks/{task_id}",
        "/api/internal/runtime-profile/apply",
        "EFP_TOOLS_DIR",
        "/app/tools",
        "EFP_SKILLS_DIR",
        "/app/skills",
        "src.tools_external",
        "src/runtime/external_tools.py",
        "allow_override",
        "EFP_EXTERNAL_TOOLS_STRICT",
        "tool_source",
        "schema_source",
        "execution_source",
        "external_shadowed_by_legacy",
        "tests/fixtures/runtime_contract",
    ]:
        assert needle in text


def test_observability_contract_doc_mentions_all_log_context_fields():
    text = Path("docs/observability_contract.md").read_text(encoding="utf-8")
    for field in [
        "trace_id",
        "span_id",
        "parent_span_id",
        "request_id",
        "session_id",
        "task_id",
        "portal_task_id",
        "portal_dispatch_id",
        "agent_id",
        "runtime_type",
        "execution_type",
        "source_type",
        "tool_name",
        "tool_source",
        "skill_name",
        "profile_version",
        "path",
    ]:
        assert field in text
    assert "RedactingFilter" in text
    assert "third-party" in text or "第三方" in text
