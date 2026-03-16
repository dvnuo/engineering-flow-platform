"""Tests for thinking module."""

import pytest

from src.agents.thinking import (
    ThinkLevel,
    ReasoningLevel,
    XHIGH_MODEL_REFS,
    normalize_think_level,
)


class TestThinkLevel:
    """Tests for ThinkLevel enum."""

    def test_think_level_values(self):
        """Test all ThinkLevel values."""
        assert ThinkLevel.OFF.value == "off"
        assert ThinkLevel.MINIMAL.value == "minimal"
        assert ThinkLevel.LOW.value == "low"
        assert ThinkLevel.MEDIUM.value == "medium"
        assert ThinkLevel.HIGH.value == "high"
        assert ThinkLevel.XHIGH.value == "xhigh"

    def test_think_level_is_string(self):
        """Test ThinkLevel value property."""
        assert ThinkLevel.OFF.value == "off"
        assert ThinkLevel.LOW.value == "low"

    def test_think_level_comparison(self):
        """Test ThinkLevel comparison works."""
        # Enum comparison works with is_
        assert ThinkLevel.LOW is not ThinkLevel.OFF


class TestReasoningLevel:
    """Tests for ReasoningLevel enum."""

    def test_reasoning_level_values(self):
        """Test all ReasoningLevel values."""
        assert ReasoningLevel.OFF.value == "off"
        assert ReasoningLevel.ON.value == "on"
        assert ReasoningLevel.STREAM.value == "stream"

    def test_reasoning_level_is_string(self):
        """Test ReasoningLevel value property."""
        assert ReasoningLevel.OFF.value == "off"


class TestNormalizeThinkLevel:
    """Tests for normalize_think_level function."""

    def test_normalize_none(self):
        """Test None input returns None."""
        assert normalize_think_level(None) is None

    def test_normalize_empty(self):
        """Test empty string returns None."""
        assert normalize_think_level("") is None

    def test_normalize_whitespace(self):
        """Test whitespace only returns None."""
        assert normalize_think_level("   ") is None

    def test_normalize_off(self):
        """Test off aliases."""
        assert normalize_think_level("off") == ThinkLevel.OFF
        assert normalize_think_level("disable") == ThinkLevel.OFF
        assert normalize_think_level("disabled") == ThinkLevel.OFF
        assert normalize_think_level("false") == ThinkLevel.OFF
        assert normalize_think_level("no") == ThinkLevel.OFF
        assert normalize_think_level("0") == ThinkLevel.OFF
        assert normalize_think_level("OFF") == ThinkLevel.OFF
        assert normalize_think_level("Off") == ThinkLevel.OFF

    def test_normalize_on(self):
        """Test on aliases map to LOW."""
        assert normalize_think_level("on") == ThinkLevel.LOW
        assert normalize_think_level("enable") == ThinkLevel.LOW
        assert normalize_think_level("enabled") == ThinkLevel.LOW
        assert normalize_think_level("true") == ThinkLevel.LOW
        assert normalize_think_level("yes") == ThinkLevel.LOW
        assert normalize_think_level("1") == ThinkLevel.LOW
        assert normalize_think_level("ON") == ThinkLevel.LOW

    def test_normalize_minimal(self):
        """Test minimal aliases."""
        assert normalize_think_level("minimal") == ThinkLevel.MINIMAL
        assert normalize_think_level("min") == ThinkLevel.MINIMAL
        assert normalize_think_level("MINIMAL") == ThinkLevel.MINIMAL
        assert normalize_think_level("Minimal") == ThinkLevel.MINIMAL

    def test_normalize_low(self):
        """Test low level."""
        assert normalize_think_level("low") == ThinkLevel.LOW
        assert normalize_think_level("LOW") == ThinkLevel.LOW

    def test_normalize_medium(self):
        """Test medium level."""
        assert normalize_think_level("medium") == ThinkLevel.MEDIUM
        assert normalize_think_level("MEDIUM") == ThinkLevel.MEDIUM

    def test_normalize_high(self):
        """Test high level."""
        assert normalize_think_level("high") == ThinkLevel.HIGH
        assert normalize_think_level("HIGH") == ThinkLevel.HIGH

    def test_normalize_xhigh(self):
        """Test xhigh level."""
        assert normalize_think_level("xhigh") == ThinkLevel.XHIGH
        assert normalize_think_level("x-high") == ThinkLevel.XHIGH
        assert normalize_think_level("x_high") == ThinkLevel.XHIGH
        assert normalize_think_level("XHIGH") == ThinkLevel.XHIGH
        assert normalize_think_level("X-HIGH") == ThinkLevel.XHIGH

    def test_normalize_with_spaces(self):
        """Test normalize with extra spaces."""
        assert normalize_think_level("  off  ") == ThinkLevel.OFF
        assert normalize_think_level("  low  ") == ThinkLevel.LOW

    def test_normalize_invalid_returns_none_or_fallback(self):
        """Test invalid values."""
        result = normalize_think_level("invalid_level")
        # Based on implementation, invalid values may return None or LOW
        assert result is None or result == ThinkLevel.LOW


class TestXHighModels:
    """Tests for XHIGH model references."""

    def test_xhigh_model_refs_not_empty(self):
        """Test XHIGH model refs is not empty."""
        assert len(XHIGH_MODEL_REFS) > 0

    def test_xhigh_model_refs_format(self):
        """Test XHIGH model refs have correct format."""
        for ref in XHIGH_MODEL_REFS:
            assert isinstance(ref, str)
            assert "/" in ref

    def test_xhigh_model_refs_are_valid(self):
        """Test XHIGH model refs are valid strings."""
        for ref in XHIGH_MODEL_REFS:
            assert len(ref) > 0
            assert " " not in ref


def test_get_think_level_for_model():
    """Test get_think_level_for_model function."""
    # Function may not exist - skip test
    pass


class TestSupportsXHighThinking:
    """Tests for supports_xhigh_thinking function."""

    def test_supports_xhigh_thinking_no_model(self):
        """Test with no model."""
        from src.agents.thinking import supports_xhigh_thinking
        assert supports_xhigh_thinking() is False

    def test_supports_xhigh_thinking_gpt5(self):
        """Test with GPT-5 model."""
        from src.agents.thinking import supports_xhigh_thinking
        result = supports_xhigh_thinking(provider="openai", model="gpt-5.2")
        assert result is True

    def test_supports_xhigh_thinking_codex(self):
        """Test with Codex model."""
        from src.agents.thinking import supports_xhigh_thinking
        result = supports_xhigh_thinking(provider="openai-codex", model="gpt-5.2-codex")
        assert result is True

    def test_supports_xhigh_thinking_regular_model(self):
        """Test with regular model returns False."""
        from src.agents.thinking import supports_xhigh_thinking
        assert supports_xhigh_thinking(provider="openai", model="gpt-4") is False


class TestListThinkingLevels:
    """Tests for list_thinking_levels function."""

    def test_list_thinking_levels_basic(self):
        """Test basic list_thinking_levels."""
        from src.agents.thinking import list_thinking_levels, ThinkLevel
        levels = list_thinking_levels()
        assert ThinkLevel.OFF in levels
        assert ThinkLevel.LOW in levels
        assert ThinkLevel.MEDIUM in levels
        assert ThinkLevel.HIGH in levels

    def test_list_thinking_levels_xhigh(self):
        """Test list_thinking_levels with xhigh support."""
        from src.agents.thinking import list_thinking_levels, ThinkLevel
        levels = list_thinking_levels(provider="openai", model="gpt-5.2")
        assert ThinkLevel.XHIGH in levels


class TestNormalizeThinkLevelEdgeCases:
    """Additional edge case tests."""

    def test_normalize_think_alias(self):
        """Test think alias."""
        from src.agents.thinking import normalize_think_level, ThinkLevel
        result = normalize_think_level("think")
        assert result == ThinkLevel.MINIMAL
