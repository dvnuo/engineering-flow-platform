import pytest

from src.sessions.manager import SessionManager


@pytest.mark.asyncio
async def test_set_context_state_persists_budget_preview_metadata_keys():
    manager = SessionManager(auto_save=False)
    manager.persistence_enabled = False

    await manager.set_context_state(
        "s1",
        {
            "summary": "summary",
            "budget": {
                "prepared_usage_percent": 49.0,
                "prepared_tokens": 98000,
                "context_window_tokens": 200000,
                "next_compaction_action": "approaching_micro_compaction",
                "tokens_until_soft_threshold": 7000,
                "tokens_until_hard_threshold": 37000,
            },
        },
    )

    session = await manager.get_session("s1")
    metadata = session.get("metadata", {})
    assert metadata.get("context_usage_percent") == 49.0
    assert metadata.get("context_estimated_tokens") == 98000
    assert metadata.get("context_window_tokens") == 200000
    assert metadata.get("context_next_compaction_action") == "approaching_micro_compaction"
