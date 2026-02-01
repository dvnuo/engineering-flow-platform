"""Tests for Jira channel adapter."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))


class TestJiraChannel:
    """Test cases for JiraChannel class."""

    def test_jira_channel_init(self):
        """Test JiraChannel initialization."""
        with patch('openclaw_mini.channel.jira.config') as mock_config:
            mock_config.jira = {
                'enabled': True,
                'base_url': 'https://test.atlassian.net',
                'email': 'test@example.com',
                'api_token': 'test_token',
                'project_key': 'TEST',
            }
            
            from openclaw_mini.channel.jira import JiraChannel
            
            channel = JiraChannel()
            assert channel.base_url == 'https://test.atlassian.net'
            assert channel.email == 'test@example.com'
            assert channel.project_key == 'TEST'
            # Auth header should be set
            assert 'Authorization' in channel.headers
            assert channel.headers['Content-Type'] == 'application/json'

    def test_jira_channel_init_defaults(self):
        """Test JiraChannel with default values."""
        with patch('openclaw_mini.channel.jira.config') as mock_config:
            mock_config.jira = {}
            
            from openclaw_mini.channel.jira import JiraChannel
            
            channel = JiraChannel()
            assert channel.base_url == ""
            assert channel.email == ""
            assert channel.project_key == ""

    def test_create_session_id(self):
        """Test session ID creation."""
        with patch('openclaw_mini.channel.jira.config') as mock_config:
            mock_config.jira = {}
            
            from openclaw_mini.channel.jira import JiraChannel
            
            channel = JiraChannel()
            session_id = channel.create_session_id("PROJ-123")
            assert session_id == "jira:PROJ-123"

    def test_handle_webhook_payload_comment(self):
        """Test handling comment webhook payload."""
        with patch('openclaw_mini.channel.jira.config') as mock_config:
            mock_config.jira = {}
            
            from openclaw_mini.channel.jira import JiraChannel
            
            channel = JiraChannel()
            payload = {
                "webhookEvent": {
                    "name": "issue_comment_created",
                    "issue": {
                        "key": "PROJ-123",
                        "fields": {
                            "summary": "Test issue"
                        }
                    },
                    "comment": {
                        "id": "10001",
                        "body": "Hello, this is a test comment",
                        "author": {
                            "displayName": "Test User",
                            "accountId": "user123"
                        },
                        "created": "2024-01-01T12:00:00.000Z"
                    }
                }
            }
            
            result = channel.handle_webhook_payload(payload)
            
            assert result is not None
            assert result["issue_key"] == "PROJ-123"
            assert result["comment_id"] == "10001"
            assert result["body"] == "Hello, this is a test comment"
            assert result["username"] == "Test User"
            assert result["event_type"] == "issue_comment_created"

    def test_handle_webhook_payload_non_comment(self):
        """Test handling non-comment webhook is ignored."""
        with patch('openclaw_mini.channel.jira.config') as mock_config:
            mock_config.jira = {}
            
            from openclaw_mini.channel.jira import JiraChannel
            
            channel = JiraChannel()
            payload = {
                "webhookEvent": {
                    "name": "issue_created",
                    "issue": {
                        "key": "PROJ-123"
                    }
                }
            }
            
            result = channel.handle_webhook_payload(payload)
            assert result is None

    def test_handle_webhook_payload_project_filter(self):
        """Test project key filtering."""
        with patch('openclaw_mini.channel.jira.config') as mock_config:
            mock_config.jira = {'project_key': 'PROJ'}
            
            from openclaw_mini.channel.jira import JiraChannel
            
            channel = JiraChannel()
            
            # Should be filtered out (wrong project)
            payload_wrong_project = {
                "webhookEvent": {
                    "name": "issue_comment_created",
                    "issue": {"key": "OTHER-123"},
                    "comment": {
                        "id": "10001",
                        "body": "Test comment",
                        "author": {"displayName": "Test User"}
                    }
                }
            }
            result = channel.handle_webhook_payload(payload_wrong_project)
            assert result is None
            
            # Should pass (correct project)
            payload_correct_project = {
                "webhookEvent": {
                    "name": "issue_comment_created",
                    "issue": {"key": "PROJ-456"},
                    "comment": {
                        "id": "10002",
                        "body": "Test comment",
                        "author": {"displayName": "Test User"}
                    }
                }
            }
            result = channel.handle_webhook_payload(payload_correct_project)
            assert result is not None
            assert result["issue_key"] == "PROJ-456"

    def test_handle_webhook_payload_adf_format(self):
        """Test handling ADF format comment body."""
        with patch('openclaw_mini.channel.jira.config') as mock_config:
            mock_config.jira = {}
            
            from openclaw_mini.channel.jira import JiraChannel
            
            channel = JiraChannel()
            payload = {
                "webhookEvent": {
                    "name": "issue_comment_created",
                    "issue": {"key": "PROJ-123"},
                    "comment": {
                        "id": "10001",
                        "body": {
                            "type": "doc",
                            "version": 1,
                            "content": [
                                {
                                    "type": "paragraph",
                                    "content": [
                                        {"type": "text", "text": "ADF formatted comment"}
                                    ]
                                }
                            ]
                        },
                        "author": {"displayName": "Test User"}
                    }
                }
            }
            
            result = channel.handle_webhook_payload(payload)
            
            assert result is not None
            assert result["body"] == "ADF formatted comment"

    @pytest.mark.asyncio
    async def test_add_comment_text_only(self):
        """Test adding a plain text comment."""
        with patch('openclaw_mini.channel.jira.config') as mock_config:
            mock_config.jira = {
                'base_url': 'https://test.atlassian.net',
                'email': 'test@example.com',
                'api_token': 'test_token',
            }
            
            from openclaw_mini.channel.jira import JiraChannel
            
            channel = JiraChannel()
            channel.session = AsyncMock()
            channel.session.request = AsyncMock(return_value=MagicMock(
                status_code=201,
                json=MagicMock(return_value={"id": "10001"})
            ))
            
            result = await channel.add_comment_text_only("PROJ-123", "Test comment")
            
            channel.session.request.assert_called_once()
            call_args = channel.session.request.call_args
            # Check positional args
            assert call_args[0][0] == "POST"  # method as first positional arg
            assert "PROJ-123" in call_args[0][1]  # URL as second positional arg
            # Check keyword args
            assert call_args.kwargs['json'] == {"body": "Test comment"}

    @pytest.mark.asyncio
    async def test_get_issue(self):
        """Test getting issue details."""
        with patch('openclaw_mini.channel.jira.config') as mock_config:
            mock_config.jira = {
                'base_url': 'https://test.atlassian.net',
                'email': 'test@example.com',
                'api_token': 'test_token',
            }
            
            from openclaw_mini.channel.jira import JiraChannel
            
            channel = JiraChannel()
            channel.session = AsyncMock()
            mock_response = {
                "key": "PROJ-123",
                "fields": {
                    "summary": "Test issue",
                    "status": {"name": "Open"}
                }
            }
            channel.session.request = AsyncMock(return_value=MagicMock(
                status_code=200,
                json=MagicMock(return_value=mock_response)
            ))
            
            result = await channel.get_issue("PROJ-123")
            
            assert result["key"] == "PROJ-123"
            assert result["fields"]["summary"] == "Test issue"


class TestJiraAuth:
    """Tests for Jira authentication."""

    def test_auth_header_format(self):
        """Test that auth header is correctly formatted."""
        import base64
        
        with patch('openclaw_mini.channel.jira.config') as mock_config:
            mock_config.jira = {
                'email': 'test@example.com',
                'api_token': 'test_token',
            }
            
            from openclaw_mini.channel.jira import JiraChannel
            
            channel = JiraChannel()
            expected_auth = base64.b64encode(b'test@example.com:test_token').decode()
            assert channel.headers["Authorization"] == f"Basic {expected_auth}"


class TestJiraAPIEndpoints:
    """Tests for Jira API endpoint construction."""

    @pytest.mark.asyncio
    async def test_search_issues(self):
        """Test issue search."""
        with patch('openclaw_mini.channel.jira.config') as mock_config:
            mock_config.jira = {
                'base_url': 'https://test.atlassian.net',
                'email': 'test@example.com',
                'api_token': 'test_token',
            }
            
            from openclaw_mini.channel.jira import JiraChannel
            
            channel = JiraChannel()
            channel.session = AsyncMock()
            mock_response = {
                "issues": [
                    {"key": "PROJ-123", "fields": {"summary": "Issue 1"}},
                    {"key": "PROJ-124", "fields": {"summary": "Issue 2"}},
                ]
            }
            channel.session.request = AsyncMock(return_value=MagicMock(
                status_code=200,
                json=MagicMock(return_value=mock_response)
            ))
            
            result = await channel.search_issues("project = PROJ")
            
            assert len(result) == 2
            assert result[0]["key"] == "PROJ-123"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
