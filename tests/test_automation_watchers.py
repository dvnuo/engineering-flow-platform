import pytest

from src.cron import automation_watchers


def test_automation_watchers_disabled_by_default():
    assert automation_watchers.is_enabled() is False


@pytest.mark.asyncio
async def test_start_automation_watchers_is_noop():
    result = await automation_watchers.start_automation_watchers()
    assert result is None


@pytest.mark.asyncio
async def test_stop_automation_watchers_handles_none_task():
    result = await automation_watchers.stop_automation_watchers(None)
    assert result is None
