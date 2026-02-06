"""Tests for skills/executor/tools.py - Function tools registration."""

import pytest
from unittest.mock import patch, AsyncMock


class TestFunctionToolsRegistration:
    """Test that all function tools are properly registered."""

    def test_function_tools_import(self):
        """Verify FUNCTION_TOOLS dictionary is populated."""
        from skills.executor.tools import FUNCTION_TOOLS
        
        # All 11 function tools should be registered
        expected_tools = [
            # Jira tools
            "jira_get_issue",
            "jira_search",
            "jira_add_comment",
            # Confluence tools
            "confluence_get_page",
            "confluence_search",
            # GitHub tools
            "github_get_issue",
            "github_search_issues",
            "github_add_comment",
            # Git tools
            "git_status",
            "git_commit",
            "git_push",
        ]
        
        assert len(FUNCTION_TOOLS) == 11, f"Expected 11 tools, got {len(FUNCTION_TOOLS)}"
        
        for tool_name in expected_tools:
            assert tool_name in FUNCTION_TOOLS, f"Tool '{tool_name}' not found in FUNCTION_TOOLS"
            assert callable(FUNCTION_TOOLS[tool_name]), f"Tool '{tool_name}' is not callable"

    def test_get_tool_names_includes_function_tools(self):
        """Verify get_tool_names() returns function tools."""
        from skills.executor.tools import get_tool_names
        
        tool_names = get_tool_names()
        
        # Should include both class-based and function-based tools
        assert "jira_get_issue" in tool_names
        assert "github_get_issue" in tool_names
        assert "git_status" in tool_names
        assert "confluence_get_page" in tool_names

    def test_get_tools_schema_includes_function_tools(self):
        """Verify get_tools_schema() includes function tool schemas."""
        from skills.executor.tools import get_tools_schema
        
        schemas = get_tools_schema()
        schema_names = [s.get("function", {}).get("name") for s in schemas]
        
        assert "jira_get_issue" in schema_names
        assert "github_get_issue" in schema_names
        assert "git_status" in schema_names

    @pytest.mark.asyncio
    async def test_execute_tool_finds_jira_tool(self):
        """Test that execute_tool can find and execute jira_get_issue."""
        from skills.executor.tools import execute_tool
        
        # Mock the jira client to avoid real API calls
        with patch('src.tools.jira.jira_client') as mock_client:
            mock_client.get_issue.return_value = {
                "key": "TEST-123",
                "summary": "Test Issue",
                "status": {"name": "Open"},
                "description": "Test description"
            }
            
            result = await execute_tool("jira_get_issue", issue_key="TEST-123")
            
            # Should not return "Tool not found" error
            assert result.error is None or "Tool not found" not in result.error
            # Should have called the mock
            mock_client.get_issue.assert_called_once_with("TEST-123")

    @pytest.mark.asyncio
    async def test_execute_tool_finds_github_tool(self):
        """Test that execute_tool can find and execute github_get_issue."""
        from skills.executor.tools import execute_tool
        
        with patch('src.tools.github.github_client') as mock_client:
            mock_client.get_issue.return_value = {
                "state": "open",
                "title": "Test Issue",
                "body": "Test body"
            }
            
            result = await execute_tool(
                "github_get_issue",
                owner="itwake",
                repo="opsclaw",
                issue_number=1
            )
            
            assert result.error is None or "Tool not found" not in result.error

    @pytest.mark.asyncio
    async def test_execute_tool_finds_git_tool(self):
        """Test that execute_tool can find and execute git_status."""
        from skills.executor.tools import execute_tool
        
        with patch('src.tools.git.git_client') as mock_client:
            mock_client.status.return_value = "On branch main"
            
            result = await execute_tool("git_status", workspace=".")
            
            assert result.error is None or "Tool not found" not in result.error
            assert mock_client.status.called

    @pytest.mark.asyncio
    async def test_execute_tool_unknown_returns_error(self):
        """Test that execute_tool returns error for unknown tools."""
        from skills.executor.tools import execute_tool
        
        result = await execute_tool("nonexistent_tool", arg="value")
        
        assert result.success is False
        assert "Tool not found" in result.error

    def test_tool_count_constant(self):
        """Verify the expected number of function tools."""
        from skills.executor.tools import FUNCTION_TOOLS
        
        # This test ensures we track expected tool count
        expected_count = 11
        actual_count = len(FUNCTION_TOOLS)
        
        assert actual_count == expected_count, (
            f"Expected {expected_count} function tools, got {actual_count}. "
            f"Available: {list(FUNCTION_TOOLS.keys())}"
        )


class TestFunctionToolsSchema:
    """Test tool schemas for function-based tools."""

    def test_jira_tools_have_schemas(self):
        """Verify Jira tools have proper OpenAI schemas."""
        from skills.executor.tools import get_tools_schema
        
        schemas = get_tools_schema()
        schema_dict = {s.get("function", {}).get("name"): s for s in schemas}
        
        # Check jira_get_issue schema exists and has required fields
        assert "jira_get_issue" in schema_dict
        schema = schema_dict["jira_get_issue"]
        params = schema.get("function", {}).get("parameters", {})
        assert "issue_key" in params.get("required", [])

    def test_github_tools_have_schemas(self):
        """Verify GitHub tools have proper OpenAI schemas."""
        from skills.executor.tools import get_tools_schema
        
        schemas = get_tools_schema()
        schema_dict = {s.get("function", {}).get("name"): s for s in schemas}
        
        assert "github_get_issue" in schema_dict
        schema = schema_dict["github_get_issue"]
        params = schema.get("function", {}).get("parameters", {})
        assert "owner" in params.get("required", [])
        assert "repo" in params.get("required", [])
        assert "issue_number" in params.get("required", [])

    def test_git_tools_have_schemas(self):
        """Verify Git tools have proper OpenAI schemas."""
        from skills.executor.tools import get_tools_schema
        
        schemas = get_tools_schema()
        schema_dict = {s.get("function", {}).get("name"): s for s in schemas}
        
        assert "git_status" in schema_dict
        assert "git_commit" in schema_dict
        assert "git_push" in schema_dict
