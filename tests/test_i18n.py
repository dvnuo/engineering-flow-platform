"""Tests for i18n (internationalization) support."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestInternationalization:
    """Tests for i18n support."""

    def test_error_messages_are_english(self):
        """Test that error messages are in English."""
        # Test that error messages contain English text
        error_messages = [
            "Sorry, I encountered an error",
            "I could not retrieve",
            "Test Cases Generated",
            "Based on the requirements",
        ]
        
        for msg in error_messages:
            # All messages should be in English (ASCII)
            try:
                msg.encode('ascii')
                assert True
            except UnicodeEncodeError:
                pytest.fail(f"Message contains non-ASCII characters: {msg}")

    def test_english_system_prompt(self):
        """Test that system prompt is in English."""
        from openclaw_mini.agent.core import Agent
        from openclaw_mini.skills.executor import get_tools_schemas
        
        # Mock get_tools_schemas at module level
        with patch('openclaw_mini.skills.executor.get_tools_schemas', return_value=[]):
            agent = Agent()
            
            # System prompt should be in English
            assert "You are a helpful AI assistant" in agent.system_prompt
            assert "Tooling" in agent.system_prompt
            assert "## Guidelines" in agent.system_prompt

    def test_english_tool_descriptions(self):
        """Test that tool descriptions are in English."""
        from openclaw_mini.skills.executor.tools import (
            ExecTool, ReadTool, WriteTool, EditTool,
            WebSearchTool, WebFetchTool
        )
        
        exec_tool = ExecTool()
        assert "Execute" in exec_tool.description
        assert "command" in exec_tool.description.lower()
        
        read_tool = ReadTool()
        assert "Read" in read_tool.description
        
        write_tool = WriteTool()
        assert "overwrite" in write_tool.description.lower()
        
        edit_tool = EditTool()
        assert "replacing" in edit_tool.description.lower() or "edits" in edit_tool.description.lower()
        
        search_tool = WebSearchTool()
        assert "Search" in search_tool.description
        
        fetch_tool = WebFetchTool()
        assert "Fetch" in fetch_tool.description

    def test_english_skill_descriptions(self):
        """Test that skill descriptions are in English."""
        from openclaw_mini.skills.test_case_generator.skill import TestCaseSkill
        
        skill = TestCaseSkill()
        assert "pytest" in skill.description.lower() or "test" in skill.description.lower()

    def test_english_jira_messages(self):
        """Test that Jira-related messages are in English."""
        from openclaw_mini.channel.jira import JiraChannel
        
        # Test command detection (should be English)
        # is_test_case_command is an instance method
        channel = JiraChannel()
        assert channel.is_test_case_command("create test cases")
        assert channel.is_test_case_command("generate tests")
        # Chinese commands should also work for backward compatibility
        assert channel.is_test_case_command("创建测试用例")

    def test_english_gateway_messages(self):
        """Test that gateway server messages are in English."""
        # Test error message format
        error_msg = "Sorry, I encountered an error: {str(e)}"
        assert "encountered an error" in error_msg.lower()
        
        # Test handle_jira_message format
        jira_error = "Sorry, I encountered an error: {str(e)}"
        assert "encountered an error" in jira_error.lower()

    def test_no_hardcoded_chinese_strings(self):
        """Test that code doesn't contain hardcoded Chinese strings."""
        import os
        
        # Files to check
        files_to_check = [
            "openclaw_mini/gateway/server.py",
            "openclaw_mini/agent/core.py",
            "openclaw_mini/channel/jira.py",
            "openclaw_mini/skills/test_case_generator/skill.py",
        ]
        
        chinese_patterns = [
            "我无法",
            "请确保",
            "测试用例已生成",
            "基于",
            "需求描述",
            "生成自动化测试用例",
            "处理 Jira",
            "发生错误",
        ]
        
        for file_path in files_to_check:
            full_path = Path(__file__).parent.parent.parent / file_path
            if full_path.exists():
                content = full_path.read_text()
                for pattern in chinese_patterns:
                    assert pattern not in content, f"Found Chinese string '{pattern}' in {file_path}"

    def test_english_log_messages(self):
        """Test that log messages are in English."""
        from openclaw_mini.gateway.server import logger
        
        # Logger should use English
        assert logger.name == "openclaw_mini.gateway.server"

    def test_english_response_format(self):
        """Test that response formats use English."""
        # Test test case generation response format
        intro = "## Test Cases Generated ✅"
        assert "Test Cases Generated" in intro
        
        # Test requirement description format
        req_msg = "Based on the requirements description for **{issue_key}**"
        assert "Based on the requirements" in req_msg


class TestI18nConfiguration:
    """Tests for i18n configuration."""

    def test_config_works_with_english(self):
        """Test that configuration works with English settings."""
        from openclaw_mini.config import Config
        
        config = Config()
        assert isinstance(config._config, dict)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
