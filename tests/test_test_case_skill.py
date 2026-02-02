"""Tests for test case generator skill."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(project_root))


class TestTestCaseSkill:
    """Test cases for TestCaseSkill class."""

    def test_skill_init(self):
        """Test skill initialization."""
        from skills.test_case_generator.skill import TestCaseSkill
        
        skill = TestCaseSkill()
        assert skill.name == "test_case_generator"
        assert "test" in skill.description.lower()

    @pytest.mark.asyncio
    async def test_generate_test_cases(self):
        """Test generating test cases from requirements."""
        from skills.test_case_generator.skill import TestCaseSkill
        
        with patch('skills.test_case_generator.skill.llm_client') as mock_llm:
            mock_llm.complete = AsyncMock(
                return_value="""import pytest

class TestUserLogin:
    def test_login_success(self):
        pass
"""
            )
            
            skill = TestCaseSkill()
            result = await skill.generate(
                requirements="用户应该能够登录系统",
                framework="pytest",
            )
            
            assert "import pytest" in result
            assert "TestUserLogin" in result or "test_" in result
            mock_llm.complete.assert_called_once()

    @pytest.mark.asyncio
    async def test_generate_with_empty_requirements(self):
        """Test error handling with empty requirements."""
        from skills.test_case_generator.skill import TestCaseSkill
        
        with patch('skills.test_case_generator.skill.llm_client') as mock_llm:
            mock_llm.complete = AsyncMock(return_value="")
            
            skill = TestCaseSkill()
            result = await skill.generate(requirements="")
            
            # Should still call LLM but return empty
            mock_llm.complete.assert_called_once()

    def test_parse_requirements_from_adf(self):
        """Test parsing ADF formatted description."""
        from skills.test_case_generator.skill import TestCaseSkill
        
        skill = TestCaseSkill()
        adf_description = {
            "type": "doc",
            "version": 1,
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {"type": "text", "text": "用户需求：第一段"},
                        {"type": "text", "text": " "},
                        {"type": "text", "text": "第二段"}
                    ]
                }
            ]
        }
        
        result = skill.parse_requirements_from_jira(adf_description)
        assert "用户需求" in result
        assert "第一段" in result
        assert "第二段" in result

    def test_parse_requirements_from_string(self):
        """Test parsing plain text description."""
        from skills.test_case_generator.skill import TestCaseSkill
        
        skill = TestCaseSkill()
        plain_description = "这是一个简单的需求描述"
        
        result = skill.parse_requirements_from_jira(plain_description)
        assert result == plain_description


class TestTestCaseCommand:
    """Test cases for test case command detection."""

    def test_is_test_case_command_chinese(self):
        """Test detecting Chinese test case commands."""
        from channel.jira import JiraChannel
        
        with patch('channel.jira.config') as mock_config:
            mock_config.jira = {}
            
            channel = JiraChannel()
            assert channel.is_test_case_command("创建测试用例") is True
            assert channel.is_test_case_command("生成测试") is True
            assert channel.is_test_case_command("请创建测试用例") is True

    def test_is_test_case_command_english(self):
        """Test detecting English test case commands."""
        from channel.jira import JiraChannel
        
        with patch('channel.jira.config') as mock_config:
            mock_config.jira = {}
            
            channel = JiraChannel()
            assert channel.is_test_case_command("create test case") is True
            assert channel.is_test_case_command("create tests") is True
            assert channel.is_test_case_command("generate test") is True

    def test_is_not_test_case_command(self):
        """Test negative case - regular messages."""
        from channel.jira import JiraChannel
        
        with patch('channel.jira.config') as mock_config:
            mock_config.jira = {}
            
            channel = JiraChannel()
            assert channel.is_test_case_command("这个功能很好用") is False
            assert channel.is_test_case_command("I have a question") is False
            assert channel.is_test_case_command("please help me") is False


class TestTestCaseIntegration:
    """Integration tests for test case generation flow."""

    @pytest.mark.asyncio
    async def test_full_generation_flow(self):
        """Test the complete flow from command to test cases."""
        from skills.test_case_generator.skill import TestCaseSkill
        from channel.jira import JiraChannel
        
        # Mock dependencies
        with patch('skills.test_case_generator.skill.llm_client') as mock_llm, \
             patch('channel.jira.config') as mock_config:
            
            mock_llm.complete = AsyncMock(
                return_value="""def test_feature():
    pass
"""
            )
            mock_config.jira = {}
            
            skill = TestCaseSkill()
            channel = JiraChannel()
            
            # Simulate command detection
            assert channel.is_test_case_command("创建测试用例")
            
            # Simulate requirements parsing
            adf_desc = {
                "type": "doc",
                "version": 1,
                "content": [{"type": "paragraph", "content": [{"type": "text", "text": "需求"}]}]
            }
            requirements = skill.parse_requirements_from_jira(adf_desc)
            assert requirements == "需求"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
