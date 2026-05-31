from src.runtime.capability_registry import (
    CapabilityDescriptor,
    DefaultCapabilityRegistry,
    _CapabilityBuilder,
    build_default_capability_registry,
)
from src.config import config


def test_registry_register_and_get_descriptor():
    registry = DefaultCapabilityRegistry()
    descriptor = CapabilityDescriptor(
        capability_id="tool:demo",
        type="tool",
        name="demo_tool",
        policy_tags=["demo"],
    )

    registry.register(descriptor)
    fetched = registry.get("tool:demo")

    assert fetched is not None
    assert fetched.capability_id == "tool:demo"
    assert fetched.name == "demo_tool"


def test_registry_deduplicates_capability_id_safely():
    registry = DefaultCapabilityRegistry()
    registry.register(CapabilityDescriptor(capability_id="Tool:Demo", type="tool", name="first"))
    registry.register(CapabilityDescriptor(capability_id=" tool:demo ", type="tool", name="second"))

    all_items = registry.list_all()
    assert len(all_items) == 1
    assert all_items[0].name == "first"


def test_registry_lists_by_type():
    registry = DefaultCapabilityRegistry()
    registry.register(CapabilityDescriptor(capability_id="skill:a", type="skill", name="a"))
    registry.register(CapabilityDescriptor(capability_id="adapter:x", type="adapter_action", name="x"))

    skills = registry.list_by_type("skill")
    adapters = registry.list_by_type("adapter_action")

    assert len(skills) == 1
    assert skills[0].capability_id == "skill:a"
    assert len(adapters) == 1


def test_default_registry_includes_skill_and_adapter_capabilities():
    registry = build_default_capability_registry()

    skills = registry.list_by_type("skill")
    adapters = registry.list_by_type("adapter_action")

    assert skills
    assert adapters
    assert any(item.capability_id.startswith("adapter:github:") for item in adapters)
    assert any(item.capability_id.startswith("adapter:jira:") for item in adapters)
    assert any(item.capability_id.startswith("adapter:portal:") for item in adapters)
    assert any(item.capability_id == "adapter:portal:create_delegation" for item in adapters)
    assert any(item.capability_id == "adapter:portal:get_specialist_pool" for item in adapters)
    assert any(item.capability_id == "adapter:portal:create_task_agent" for item in adapters)
    assert any(item.capability_id == "adapter:portal:delete_task_agent" for item in adapters)
    create_task_agent_descriptor = next(item for item in adapters if item.capability_id == "adapter:portal:create_task_agent")
    required = set(create_task_agent_descriptor.input_schema.get("required", []))
    assert {"group_id", "leader_agent_id", "template_agent_id", "name"}.issubset(required)
    portal_actions = [item for item in adapters if item.capability_id.startswith("adapter:portal:")]
    assert portal_actions
    assert all(item.metadata.get("internal_portal_api") is True for item in portal_actions)
    assert any(item.capability_id == "adapter:jira:add_comment" for item in adapters)
    export_descriptor = next(item for item in adapters if item.capability_id == "adapter:jira:export_issues_to_markdown")
    assert "filesystem_write" in export_descriptor.policy_tags
    assert "attachment_download" in export_descriptor.policy_tags
    assert "warnings" in export_descriptor.output_schema.get("properties", {})


def test_descriptor_fields_preserved():
    registry = DefaultCapabilityRegistry()
    descriptor = CapabilityDescriptor(
        capability_id="adapter:identity",
        type="adapter_action",
        name="identity_action",
        policy_tags=["secure", "write"],
        requires_identity_binding=True,
        enabled=False,
        metadata={"x": 1},
    )
    registry.register(descriptor)

    fetched = registry.get("adapter:identity")
    assert fetched is not None
    assert fetched.requires_identity_binding is True
    assert fetched.enabled is False
    assert fetched.policy_tags == ["secure", "write"]
    assert fetched.metadata == {"x": 1}


def test_registry_export_catalog_and_filters():
    registry = build_default_capability_registry()

    catalog = registry.export_catalog()
    assert isinstance(catalog, list)
    assert catalog
    first = catalog[0]
    assert {
        "capability_id",
        "type",
        "name",
        "logical_name",
        "action_alias",
        "adapter_system",
        "input_schema",
        "output_schema",
        "policy_tags",
        "requires_identity_binding",
        "enabled",
        "source_ref",
        "metadata",
    }.issubset(set(first.keys()))
    assert registry.exists(first["capability_id"]) is True
    assert registry.list_enabled()
    assert registry.list_by_type("adapter_action")
    assert registry.list_by_type("skill")
    assert registry.list_by_type("tool")


def test_registry_collects_adapter_channel_tool_skill_types():
    registry = build_default_capability_registry()
    types = {item.type for item in registry.list_all()}
    assert "adapter_action" in types
    assert "skill" in types
    assert "tool" in types
    assert "channel_action" in types


def test_registry_export_catalog_snapshot_has_deterministic_version():
    registry = build_default_capability_registry()
    snapshot_a = registry.export_catalog_snapshot()
    snapshot_b = registry.export_catalog_snapshot()
    assert snapshot_a["catalog_version"] == snapshot_b["catalog_version"]
    assert snapshot_a["count"] == len(snapshot_a["capabilities"])
    assert isinstance(snapshot_a["generated_at"], str) and snapshot_a["generated_at"].endswith("Z")


def test_registry_adapter_action_entries_include_alias_and_system():
    registry = build_default_capability_registry()
    adapter_entry = next(item for item in registry.export_catalog() if item["type"] == "adapter_action")
    assert adapter_entry["action_alias"]
    assert adapter_entry["adapter_system"] in {"github", "jira", "portal"}


def test_capability_registry_uses_runtime_tool_catalog_even_when_llm_tools_restricted(monkeypatch):
    monkeypatch.setitem(config._config, "llm", {"tools": ["bash"]})
    from src import get_tools_schema

    all_tool_names = {
        (item.get("function", {}) or {}).get("name") or item.get("name")
        for item in get_tools_schema()
        if isinstance(item, dict)
    }
    all_tool_names.discard(None)

    registry = build_default_capability_registry()
    registry_tool_names = {item.name for item in registry.list_by_type("tool")}
    assert all_tool_names.issubset(registry_tool_names)



def test_default_registry_includes_triggered_event_skill_and_github_reply_review_comment_adapter():
    registry = build_default_capability_registry()
    assert registry.exists("skill:handle-triggered-event") is True
    assert registry.exists("adapter:github:reply_review_comment") is True


def test_default_registry_includes_github_add_commit_comment_adapter():
    registry = build_default_capability_registry()
    assert registry.exists("adapter:github:add_commit_comment") is True


def test_default_registry_includes_github_add_discussion_comment_adapter():
    registry = build_default_capability_registry()
    assert registry.exists("adapter:github:add_discussion_comment") is True


def test_capability_registry_tool_schema_supports_function_nested_parameters(monkeypatch):
    tool_schema = {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "x",
            "parameters": {"type": "object", "properties": {"command": {"type": "string"}}},
        },
        "metadata": {"tool_id": "bash", "tool_source": "efp_runtime"},
    }

    monkeypatch.setattr("src.get_tools_schema", lambda: [tool_schema])
    builder = _CapabilityBuilder(DefaultCapabilityRegistry())
    builder._register_tools()
    descriptor = builder.registry.get("tool:bash")
    assert descriptor is not None
    assert descriptor.input_schema.get("properties", {}).get("command", {}).get("type") == "string"
    assert descriptor.metadata.get("tool_id") == "bash"
    assert descriptor.metadata.get("tool_source") == "efp_runtime"
