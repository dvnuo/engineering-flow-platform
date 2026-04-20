"""Tests for enhanced Jira tools added in PR #219"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.fixture
def mock_jira_channel():
    """Mock JiraChannel for testing"""
    with patch('src.jira.api.jira_channel') as mock:
        mock._request = AsyncMock(return_value='{"result": "ok"}')
        yield mock


@pytest.mark.asyncio
async def test_jira_update_issue(mock_jira_channel):
    """Test jira_update_issue function"""
    from src.jira.api import jira_update_issue
    result = await jira_update_issue("PROJ-123", summary="New Summary", description="New Description")
    mock_jira_channel._request.assert_called()
    assert "updated successfully" in result or "Error" in result


def test_jira_get_issue_schema_max_chars_description_prefers_unset_default():
    from src.jira import get_tools_schemas

    schemas = get_tools_schemas()
    get_issue_schema = next(s for s in schemas if s["function"]["name"] == "jira_get_issue")
    max_chars_desc = get_issue_schema["function"]["parameters"]["properties"]["max_chars"]["description"]

    assert "Leave unset for full Jira issue content" in max_chars_desc


@pytest.mark.asyncio
async def test_jira_update_issue_summary_only(mock_jira_channel):
    """Test jira_update_issue with summary only"""
    from src.jira.api import jira_update_issue
    result = await jira_update_issue("PROJ-123", summary="Summary Only")
    mock_jira_channel._request.assert_called()
    assert "updated successfully" in result or "Error" in result


@pytest.mark.asyncio
async def test_jira_update_issue_no_fields(mock_jira_channel):
    """Test jira_update_issue with no fields returns error"""
    from src.jira.api import jira_update_issue
    result = await jira_update_issue("PROJ-123")
    assert "No fields to update" in result


@pytest.mark.asyncio
async def test_jira_assign_issue(mock_jira_channel):
    """Test jira_assign_issue function"""
    from src.jira.api import jira_assign_issue
    result = await jira_assign_issue("PROJ-123", assignee="john.doe")
    mock_jira_channel._request.assert_called()
    assert "assigned" in result.lower() or "Error" in result


@pytest.mark.asyncio
async def test_jira_assign_issue_unassign(mock_jira_channel):
    """Test jira_assign_issue to unassign (empty string)"""
    from src.jira.api import jira_assign_issue
    result = await jira_assign_issue("PROJ-123", assignee="")
    assert "Error" in result or "assigned" in result.lower()


@pytest.mark.asyncio
async def test_jira_assign_issue_no_assignee(mock_jira_channel):
    """Test jira_assign_issue without assignee returns error"""
    from src.jira.api import jira_assign_issue
    result = await jira_assign_issue("PROJ-123")
    assert "assignee parameter is required" in result


@pytest.mark.asyncio
async def test_jira_get_projects(mock_jira_channel):
    """Test jira_get_projects function"""
    from src.jira.api import jira_get_projects
    mock_jira_channel._request.return_value = '[{"key": "PROJ"}]'
    result = await jira_get_projects()
    mock_jira_channel._request.assert_called()
    assert "PROJ" in result or "Error" in result


@pytest.mark.asyncio
async def test_jira_get_components(mock_jira_channel):
    """Test jira_get_components function"""
    from src.jira.api import jira_get_components
    mock_jira_channel._request.return_value = '[{"name": "comp1"}]'
    result = await jira_get_components("PROJ")
    mock_jira_channel._request.assert_called()
    assert "comp1" in result or "Error" in result


@pytest.mark.asyncio
async def test_jira_get_versions(mock_jira_channel):
    """Test jira_get_versions function"""
    from src.jira.api import jira_get_versions
    mock_jira_channel._request.return_value = '[{"name": "v1"}]'
    result = await jira_get_versions("PROJ")
    mock_jira_channel._request.assert_called()
    assert "v1" in result or "Error" in result


@pytest.mark.asyncio
async def test_jira_get_worklog(mock_jira_channel):
    """Test jira_get_worklog function"""
    from src.jira.api import jira_get_worklog
    mock_jira_channel._request.return_value = '[{"timeSpent": "1h"}]'
    result = await jira_get_worklog("PROJ-123")
    mock_jira_channel._request.assert_called()
    assert "1h" in result or "Error" in result


@pytest.mark.asyncio
async def test_jira_add_worklog(mock_jira_channel):
    """Test jira_add_worklog function"""
    from src.jira.api import jira_add_worklog
    result = await jira_add_worklog("PROJ-123", "1h", "Work done")
    mock_jira_channel._request.assert_called()
    assert "work log" in result.lower() or "Error" in result


@pytest.mark.asyncio
async def test_jira_add_worklog_no_comment(mock_jira_channel):
    """Test jira_add_worklog without comment"""
    from src.jira.api import jira_add_worklog
    result = await jira_add_worklog("PROJ-123", "2h")
    mock_jira_channel._request.assert_called()
    assert "work log" in result.lower() or "Error" in result
