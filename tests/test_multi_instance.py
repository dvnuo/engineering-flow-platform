"""Tests for multi-instance URL lookup functionality"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestJiraGetIssueByUrl:
    """Tests for jira_get_issue_by_url with multi-instance support"""
    
    @pytest.mark.asyncio
    async def test_jira_get_issue_by_url_extracts_key(self):
        """Test jira_get_issue_by_url extracts issue key from URL"""
        # Patch at the location where it's used in __init__.py
        with patch('src.jira.jira_channel') as mock_channel:
            # Create mock channel that get_instance_client returns
            mock_instance = MagicMock()
            mock_instance.is_configured.return_value = True
            mock_instance.get_issue = AsyncMock(return_value={
                "fields": {
                    "status": {"name": "Open"},
                    "summary": "Test Issue",
                    "description": "Test description",
                    "assignee": None,
                    "priority": {"name": "High"},
                    "issuetype": {"name": "Task"},
                    "created": "2024-01-01T00:00:00.000Z",
                    "updated": "2024-01-01T00:00:00.000Z",
                }
            })
            mock_instance._parse_body = MagicMock(return_value="Test description")
            mock_instance.api_version = "2"
            
            mock_channel.get_instance_client.return_value = mock_instance
            
            from src.jira import jira_get_issue_by_url
            result = await jira_get_issue_by_url("https://company.atlassian.net/browse/PROJ-123")
            
            # Should have called get_instance_client with the URL
            mock_channel.get_instance_client.assert_called_with(url="https://company.atlassian.net/browse/PROJ-123")
            
            # Result should contain the issue info
            assert "PROJ-123" in result
            assert "Test Issue" in result
    
    @pytest.mark.asyncio
    async def test_jira_get_issue_by_url_invalid_url(self):
        """Test jira_get_issue_by_url returns error for invalid URL"""
        from src.jira import jira_get_issue_by_url
        result = await jira_get_issue_by_url("https://invalid.com/browse/")
        
        # Should return error about extracting issue key
        assert "Could not extract issue key" in result


class TestConfluenceGetPageByUrl:
    """Tests for confluence_get_page_by_url with multi-instance support"""
    
    @pytest.mark.asyncio
    async def test_confluence_get_page_by_url_extracts_id(self):
        """Test confluence_get_page_by_url extracts page ID from URL"""
        with patch('src.confluence.confluence_channel') as mock_channel:
            # Create mock channel that get_instance_client returns
            mock_instance = MagicMock()
            mock_instance.is_configured.return_value = True
            mock_instance.get_page = AsyncMock(return_value={
                "title": "Test Page",
                "body": {"storage": {"value": "Test content"}}
            })
            
            mock_channel.get_instance_client.return_value = mock_instance
            
            from src.confluence import confluence_get_page_by_url
            result = await confluence_get_page_by_url("https://company.atlassian.net/wiki/spaces/SPACE/pages/123456/Page-Title")
            
            # Should have called get_instance_client with the URL
            mock_channel.get_instance_client.assert_called_with(
                url="https://company.atlassian.net/wiki/spaces/SPACE/pages/123456/Page-Title",
                strict=True,
            )
            
            # Result should contain the page info
            assert "Test Page" in result
    
    @pytest.mark.asyncio
    async def test_confluence_get_page_by_url_invalid_url(self):
        """Test confluence_get_page_by_url returns error for invalid URL"""
        with patch('src.confluence.confluence_channel') as mock_channel:
            mock_channel.is_configured.return_value = True
            from src.confluence import confluence_get_page_by_url
            result = await confluence_get_page_by_url("https://invalid.com/")
        
        # Should return error about extracting page ID
        assert "Could not extract page ID" in result
