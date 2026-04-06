from src.runtime.capability_registry import (
    CapabilityDescriptor,
    DefaultCapabilityRegistry,
    build_default_capability_registry,
)


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
