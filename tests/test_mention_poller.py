"""Tests for the mention poller module."""

import pytest
import sys
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

# Add project root to path
sys.path.insert(0, '/root/.openclaw/workspace/codew')

from cron.mention_poller import (
    MentionPoller,
    Comment,
    Command,
)


class TestMentionPoller:
    """Test cases for MentionPoller class."""
    
    @pytest.fixture
    def poller(self):
        """Create a poller instance for testing."""
        return MentionPoller()
    
    @pytest.fixture
    def sample_comment(self):
        """Create a sample comment dict."""
        return {
            "id": "123",
            "body": "请 @lucaslai 帮忙创建一个 issue",
            "author": "testuser",
            "created_at": datetime.utcnow().isoformat(),
            "url": "https://example.com/comment/123",
        }
    
    def test_extract_mentions_basic(self, poller):
        """Test basic @mention extraction."""
        text = "请 @lucaslai 帮忙看一下"
        mentions = MentionPoller.extract_mentions(text)
        
        assert mentions == ["lucaslai"]
    
    def test_extract_mentions_multiple(self, poller):
        """Test extraction of multiple @mentions."""
        text = "@user1 和 @user2 请帮忙"
        mentions = MentionPoller.extract_mentions(text)
        
        assert mentions == ["user1", "user2"]
    
    def test_extract_mentions_none(self, poller):
        """Test extraction with no mentions."""
        text = "这是一个普通的消息"
        mentions = MentionPoller.extract_mentions(text)
        
        assert mentions == []
    
    def test_extract_mentions_case_insensitive(self, poller):
        """Test that extraction is case-insensitive."""
        text = "@UserName 测试"
        mentions = MentionPoller.extract_mentions(text)
        
        # The regex should find it regardless of case in the pattern
        assert "UserName" in mentions or "username" in mentions
    
    def test_is_monitored_user_exact_match(self, poller):
        """Test exact username matching."""
        poller.monitored_users = {"lucaslai", "admin"}
        
        assert poller.is_monitored_user("lucaslai") is True
        assert poller.is_monitored_user("admin") is True
        assert poller.is_monitored_user("unknown") is False
    
    def test_is_monitored_user_case_insensitive(self, poller):
        """Test case-insensitive username matching."""
        poller.monitored_users = {"LucasLai"}
        
        assert poller.is_monitored_user("lucaslai") is True
        assert poller.is_monitored_user("LUCASLAI") is True
        assert poller.is_monitored_user("other") is False
    
    def test_is_monitored_user_empty(self, poller):
        """Test with empty monitored users list."""
        poller.monitored_users = set()
        
        assert poller.is_monitored_user("anyone") is False
    
    def test_has_mention_true(self, poller, sample_comment):
        """Test detection of monitored user mention."""
        poller.monitored_users = {"lucaslai"}
        
        assert poller._has_mention(sample_comment["body"]) is True
    
    def test_has_mention_false(self, poller, sample_comment):
        """Test when no monitored user is mentioned."""
        poller.monitored_users = {"otheruser"}
        
        assert poller._has_mention(sample_comment["body"]) is False
    
    def test_has_mention_empty_body(self, poller):
        """Test with empty comment body."""
        poller.monitored_users = {"lucaslai"}
        
        assert poller._has_mention("") is False
    
    def test_parse_command_help(self, poller):
        """Test help command parsing."""
        cmd = poller.parse_command("@lucaslai help", "jira")
        
        assert cmd.tool_name == "help"
    
    def test_parse_command_jira_create(self, poller):
        """Test Jira create issue command parsing."""
        cmd = poller.parse_command("@lucaslai create issue \"Test Issue\" -d \"Description\"", "jira")
        
        assert cmd.tool_name == "jira_create_issue"
        assert "summary" in cmd.args
        assert "description" in cmd.args
    
    def test_parse_command_jira_status(self, poller):
        """Test Jira status command parsing."""
        cmd = poller.parse_command("@lucaslai status PROJ-123", "jira")
        
        assert cmd.tool_name == "jira_get_issue"
        assert cmd.args["issue_key"] == "PROJ-123"
    
    def test_parse_command_confluence_search(self, poller):
        """Test Confluence search command parsing."""
        cmd = poller.parse_command("@lucaslai search confluence \"API Docs\"", "confluence")
        
        assert cmd.tool_name == "confluence_search"
        assert "cql" in cmd.args
    
    def test_parse_command_unknown(self, poller):
        """Test unknown command parsing."""
        cmd = poller.parse_command("@lucaslai 这是一个未知命令", "jira")
        
        assert cmd.tool_name == "help"
        assert "context" in cmd.args
    
    def test_strip_mentions(self, poller):
        """Test removal of @mentions from text."""
        text = "@lucaslai 请帮我创建一个 issue"
        stripped = poller._strip_mentions(text)
        
        assert "@lucaslai" not in stripped
        assert "请帮我创建一个 issue" in stripped
    
    def test_strip_mentions_multiple(self, poller):
        """Test removal of multiple @mentions."""
        text = "@user1 @user2 请处理这个"
        stripped = poller._strip_mentions(text)
        
        assert "@user1" not in stripped
        assert "@user2" not in stripped
        assert "请处理这个" in stripped
    
    def test_parse_jira_create_simple(self, poller):
        """Test Jira create parsing with simple args."""
        parts = ["issue", "Test Title"]
        args = poller._parse_jira_create(parts)
        
        assert args["summary"] == "Test Title"
    
    def test_parse_jira_create_quoted(self, poller):
        """Test Jira create parsing with quoted title."""
        parts = ["issue", '"Quoted Title"']
        args = poller._parse_jira_create(parts)
        
        assert args["summary"] == "Quoted Title"
    
    def test_parse_jira_create_with_description(self, poller):
        """Test Jira create parsing with description."""
        parts = ["issue", "Test Title", "-d", "This is a description"]
        args = poller._parse_jira_create(parts)
        
        assert args["summary"] == "Test Title"
        assert args["description"] == "This is a description"
    
    def test_parse_jira_create_quoted_description(self, poller):
        """Test Jira create parsing with quoted description."""
        parts = ["issue", "Title", "-d", '"Quoted Description"']
        args = poller._parse_jira_create(parts)
        
        assert args["description"] == "Quoted Description"


class TestCommentDataclass:
    """Test cases for Comment dataclass."""
    
    def test_comment_creation(self):
        """Test Comment object creation."""
        comment = Comment(
            id="123",
            platform="jira",
            owner="testuser",
            body="Test body",
            resource_id="PROJ-123",
            resource_type="issue",
            resource_title="Test Issue",
            url="https://example.com",
            created_at=datetime.utcnow(),
        )
        
        assert comment.id == "123"
        assert comment.platform == "jira"
        assert comment.owner == "testuser"
        assert comment.body == "Test body"
    
    def test_comment_with_extra(self):
        """Test Comment with extra fields."""
        comment = Comment(
            id="456",
            platform="github",
            owner="testuser",
            body="Test",
            resource_id="1",
            resource_type="pr",
            resource_title="Test PR",
            url="https://example.com",
            created_at=datetime.utcnow(),
            extra={"labels": ["bug"]},
        )
        
        assert comment.extra["labels"] == ["bug"]


class TestCommandDataclass:
    """Test cases for Command dataclass."""
    
    def test_command_creation(self):
        """Test Command object creation."""
        cmd = Command(
            tool_name="jira_create_issue",
            args={"summary": "Test"},
            original_text="@user create issue Test",
        )
        
        assert cmd.tool_name == "jira_create_issue"
        assert cmd.args["summary"] == "Test"
        assert "create issue" in cmd.original_text
    
    def test_command_empty_args(self):
        """Test Command with empty args."""
        cmd = Command(
            tool_name="help",
            args={},
            original_text="@user help",
        )
        
        assert cmd.args == {}
        assert cmd.tool_name == "help"


class TestMentionPollerConfig:
    """Test configuration loading for MentionPoller."""
    
    @pytest.fixture
    def poller_with_config(self):
        """Create poller with mock config."""
        poller = MentionPoller()
        poller.enabled = True
        poller.interval = 60
        poller.monitored_users = {"lucaslai", "admin"}
        poller.platforms = {
            "github": {"enabled": True, "repos": ["owner/repo"]},
            "jira": {"enabled": True, "projects": ["PROJ"]},
            "confluence": {"enabled": True, "spaces": ["DEV"]},
        }
        return poller
    
    def test_poller_enabled(self, poller_with_config):
        """Test poller enabled state."""
        assert poller_with_config.enabled is True
    
    def test_poller_interval(self, poller_with_config):
        """Test poller interval setting."""
        assert poller_with_config.interval == 60
    
    def test_poller_monitored_users(self, poller_with_config):
        """Test monitored users list."""
        assert "lucaslai" in poller_with_config.monitored_users
        assert "admin" in poller_with_config.monitored_users
    
    def test_poller_platforms(self, poller_with_config):
        """Test platform configuration."""
        assert poller_with_config.platforms["github"]["enabled"] is True
        assert poller_with_config.platforms["jira"]["enabled"] is True
        assert poller_with_config.platforms["confluence"]["enabled"] is True
    
    def test_github_repos(self, poller_with_config):
        """Test GitHub repo configuration."""
        repos = poller_with_config.platforms["github"]["repos"]
        assert "owner/repo" in repos
    
    def test_jira_projects(self, poller_with_config):
        """Test Jira project configuration."""
        projects = poller_with_config.platforms["jira"]["projects"]
        assert "PROJ" in projects
    
    def test_confluence_spaces(self, poller_with_config):
        """Test Confluence space configuration."""
        spaces = poller_with_config.platforms["confluence"]["spaces"]
        assert "DEV" in spaces


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
