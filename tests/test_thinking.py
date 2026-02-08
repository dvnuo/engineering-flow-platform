"""Unit tests for agent/thinking.py - Thinking levels module."""

import pytest
import sys
sys.path.insert(0, '/root/.efp/workspace/engineering-flow-platform')

from src.agents.thinking import (
    ThinkLevel,
    ReasoningLevel,
    normalize_think_level,
    supports_xhigh_thinking,
    list_thinking_levels,
    format_thinking_levels,
    format_runtime_info,
    XHIGH_MODEL_REFS,
)


class TestThinkLevel:
    """Test ThinkLevel enum values."""

    def test_think_level_values(self):
        """Verify all think level values exist."""
        assert ThinkLevel.OFF == "off"
        assert ThinkLevel.MINIMAL == "minimal"
        assert ThinkLevel.LOW == "low"
        assert ThinkLevel.MEDIUM == "medium"
        assert ThinkLevel.HIGH == "high"
        assert ThinkLevel.XHIGH == "xhigh"

    def test_think_level_count(self):
        """Verify we have 6 thinking levels."""
        levels = list(ThinkLevel)
        assert len(levels) == 6


class TestNormalizeThinkLevel:
    """Test normalize_think_level function."""

    def test_none_input(self):
        """Test None input returns None."""
        assert normalize_think_level(None) is None

    def test_empty_string(self):
        """Test empty string returns None."""
        assert normalize_think_level("") is None

    def test_off_variants(self):
        """Test off variants."""
        assert normalize_think_level("off") == ThinkLevel.OFF
        assert normalize_think_level("OFF") == ThinkLevel.OFF
        assert normalize_think_level("disable") == ThinkLevel.OFF
        assert normalize_think_level("disabled") == ThinkLevel.OFF
        assert normalize_think_level("false") == ThinkLevel.OFF
        assert normalize_think_level("no") == ThinkLevel.OFF
        assert normalize_think_level("0") == ThinkLevel.OFF

    def test_on_variants(self):
        """Test on variants map to LOW."""
        assert normalize_think_level("on") == ThinkLevel.LOW
        assert normalize_think_level("ON") == ThinkLevel.LOW
        assert normalize_think_level("enable") == ThinkLevel.LOW
        assert normalize_think_level("enabled") == ThinkLevel.LOW
        assert normalize_think_level("true") == ThinkLevel.LOW
        assert normalize_think_level("yes") == ThinkLevel.LOW
        assert normalize_think_level("1") == ThinkLevel.LOW

    def test_minimal_variants(self):
        """Test minimal variants."""
        assert normalize_think_level("minimal") == ThinkLevel.MINIMAL
        assert normalize_think_level("min") == ThinkLevel.MINIMAL
        assert normalize_think_level("MINIMAL") == ThinkLevel.MINIMAL

    def test_low_variants(self):
        """Test low variants."""
        assert normalize_think_level("low") == ThinkLevel.LOW
        assert normalize_think_level("LOW") == ThinkLevel.LOW
        assert normalize_think_level("thinkhard") == ThinkLevel.LOW
        assert normalize_think_level("think-hard") == ThinkLevel.LOW
        assert normalize_think_level("think_hard") == ThinkLevel.LOW

    def test_medium_variants(self):
        """Test medium variants."""
        assert normalize_think_level("medium") == ThinkLevel.MEDIUM
        assert normalize_think_level("mid") == ThinkLevel.MEDIUM
        assert normalize_think_level("med") == ThinkLevel.MEDIUM
        assert normalize_think_level("MEDIUM") == ThinkLevel.MEDIUM
        assert normalize_think_level("thinkharder") == ThinkLevel.MEDIUM
        assert normalize_think_level("think-harder") == ThinkLevel.MEDIUM

    def test_high_variants(self):
        """Test high variants."""
        assert normalize_think_level("high") == ThinkLevel.HIGH
        assert normalize_think_level("HIGH") == ThinkLevel.HIGH
        assert normalize_think_level("ultra") == ThinkLevel.HIGH
        assert normalize_think_level("ultrathink") == ThinkLevel.HIGH
        assert normalize_think_level("max") == ThinkLevel.HIGH

    def test_xhigh_variants(self):
        """Test xhigh variants."""
        assert normalize_think_level("xhigh") == ThinkLevel.XHIGH
        assert normalize_think_level("XHIGH") == ThinkLevel.XHIGH
        assert normalize_think_level("x-high") == ThinkLevel.XHIGH
        assert normalize_think_level("x_high") == ThinkLevel.XHIGH

    def test_default_think_alias(self):
        """Test 'think' maps to MINIMAL."""
        assert normalize_think_level("think") == ThinkLevel.MINIMAL

    def test_invalid_input(self):
        """Test invalid input returns None."""
        assert normalize_think_level("banana") is None
        assert normalize_think_level("invalid") is None
        assert normalize_think_level("super_high") is None

    def test_whitespace_handling(self):
        """Test whitespace is stripped."""
        assert normalize_think_level("  high  ") == ThinkLevel.HIGH
        assert normalize_think_level("\tmedium\n") == ThinkLevel.MEDIUM


class TestSupportsXHighThinking:
    """Test supports_xhigh_thinking function."""

    def test_no_model_returns_false(self):
        """Test None model returns False."""
        assert supports_xhigh_thinking(None, None) is False
        assert supports_xhigh_thinking("openai", None) is False
        assert supports_xhigh_thinking(None, "") is False

    def test_gpt5_models(self):
        """Test GPT-5.2 models are recognized."""
        assert supports_xhigh_thinking("openai", "gpt-5.2") is True
        assert supports_xhigh_thinking("openai-codex", "gpt-5.2-codex") is True
        assert supports_xhigh_thinking("openai-codex", "gpt-5.1-codex") is True

    def test_case_insensitive(self):
        """Test matching is case insensitive."""
        assert supports_xhigh_thinking("OPENAI", "GPT-5.2") is True
        assert supports_xhigh_thinking("OpenAI", "Gpt-5.2") is True

    def test_regular_models_return_false(self):
        """Test non-xhigh models return False."""
        assert supports_xhigh_thinking("openai", "gpt-4") is False
        assert supports_xhigh_thinking("anthropic", "claude-sonnet-4") is False
        assert supports_xhigh_thinking("openai", "o3-mini") is False


class TestListThinkingLevels:
    """Test list_thinking_levels function."""

    def test_default_no_xhigh(self):
        """Test default levels without xhigh support."""
        levels = list_thinking_levels()
        assert ThinkLevel.OFF in levels
        assert ThinkLevel.MINIMAL in levels
        assert ThinkLevel.LOW in levels
        assert ThinkLevel.MEDIUM in levels
        assert ThinkLevel.HIGH in levels
        assert len(levels) == 5

    def test_with_xhigh_model(self):
        """Test xhigh is added for supported models."""
        levels = list_thinking_levels("openai", "gpt-5.2")
        assert ThinkLevel.XHIGH in levels
        assert len(levels) == 6

    def test_without_xhigh_model(self):
        """Test xhigh is NOT added for regular models."""
        levels = list_thinking_levels("openai", "gpt-4")
        assert ThinkLevel.XHIGH not in levels
        assert len(levels) == 5


class TestFormatThinkingLevels:
    """Test format_thinking_levels function."""

    def test_default_format(self):
        """Test default comma-separated format."""
        result = format_thinking_levels()
        assert "off" in result
        assert "minimal" in result
        assert "low" in result
        assert "medium" in result
        assert "high" in result

    def test_custom_separator(self):
        """Test custom separator."""
        result = format_thinking_levels(separator=" | ")
        assert " | " in result

    def test_with_xhigh(self):
        """Test format includes xhigh for supported models."""
        result = format_thinking_levels("openai", "gpt-5.2")
        assert "xhigh" in result


class TestFormatRuntimeInfo:
    """Test format_runtime_info function."""

    def test_basic_runtime(self):
        """Test basic runtime info output."""
        result = format_runtime_info(
            host="engineering-flow-platform",
            os_info="Linux 5.14.0",
            arch="x86_64",
            node="3.12.0",
        )
        assert "host=engineering-flow-platform" in result
        assert "os=Linux 5.14.0" in result
        assert "node=3.12.0" in result
        assert "thinking=off" in result  # default

    def test_thinking_level_in_output(self):
        """Test thinking level is included."""
        result = format_runtime_info(think_level=ThinkLevel.HIGH)
        assert "thinking=high" in result

        result = format_runtime_info(think_level=ThinkLevel.LOW)
        assert "thinking=low" in result

    def test_model_in_output(self):
        """Test model info is included."""
        result = format_runtime_info(model="claude-sonnet-4")
        assert "model=claude-sonnet-4" in result

    def test_default_model_in_output(self):
        """Test default model info is included."""
        result = format_runtime_info(default_model="default")
        assert "default_model=default" in result

    def test_channel_in_output(self):
        """Test channel info is included."""
        result = format_runtime_info(channel="discord")
        assert "channel=discord" in result

    def test_capabilities_in_output(self):
        """Test capabilities info is included."""
        result = format_runtime_info(
            channel="discord",
            capabilities=["react", "edit"]
        )
        assert "capabilities=react,edit" in result

    def test_arch_suffix_in_os(self):
        """Test arch is shown as suffix to os."""
        result = format_runtime_info(
            os_info="Linux 5.14.0",
            arch="x86_64",
        )
        assert "os=Linux 5.14.0 (x86_64)" in result

    def test_pipe_separator(self):
        """Test parts are separated by pipes."""
        result = format_runtime_info(host="test")
        assert " | " in result

    def test_empty_values_skipped(self):
        """Test empty values are not included."""
        result = format_runtime_info(host="test", os_info="", arch="", node="")
        assert "test" in result


class TestXHighModelRefs:
    """Test XHIGH_MODEL_REFS constant."""

    def test_contains_expected_models(self):
        """Verify xhigh models list."""
        assert "openai/gpt-5.2" in XHIGH_MODEL_REFS
        assert "openai-codex/gpt-5.2-codex" in XHIGH_MODEL_REFS
        assert "openai-codex/gpt-5.1-codex" in XHIGH_MODEL_REFS


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
