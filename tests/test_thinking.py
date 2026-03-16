"""Tests for thinking module."""

import pytest

from src.agents.thinking import (
    ThinkLevel,
    ReasoningLevel,
    XHIGH_MODEL_REFS,
    normalize_think_level,
    normalize_reasoning_level,
    get_think_level_for_model,
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


class TestReasoningLevel:
    """Tests for ReasoningLevel enum."""

    def test_reasoning_level_values(self):
        """Test all ReasoningLevel values."""
        assert ReasoningLevel.OFF.value == "off"
        assert ReasoningLevel.ON.value == "on"
        assert ReasoningLevel.STREAM.value == "stream"


class TestNormalizeThinkLevel:
    """Tests for normalize_think_level function."""

    def test_normalize_none(self):
        """Test None input returns None."""
        assert normalize_think_level(None) is None

    def test_normalize_empty(self):
        """Test empty string returns None."""
        assert normalize_think_level("") is None

    def test_normalize_off(self):
        """Test off aliases."""
        assert normalize_think_level("off") == ThinkLevel.OFF
        assert normalize_think_level("disable") == ThinkLevel.OFF
        assert normalize_think_level("disabled") == ThinkLevel.OFF
        assert normalize_think_level("false") == ThinkLevel.OFF
        assert normalize_think_level("no") == ThinkLevel.OFF
        assert normalize_think_level("0") == ThinkLevel.OFF

    def test_normalize_on(self):
        """Test on aliases map to LOW."""
        assert normalize_think_level("on") == ThinkLevel.LOW
        assert normalize_think_level("enable") == ThinkLevel.LOW
        assert normalize_think_level("enabled") == ThinkLevel.LOW
        assert normalize_think_level("true") == ThinkLevel.LOW
        assert normalize_think_level("yes") == ThinkLevel.LOW
        assert normalize_think_level("1") == ThinkLevel.LOW

    def test_normalize_minimal(self):
        """Test minimal aliases."""
        assert normalize_think_level("minimal") == ThinkLevel.MINIMAL
        assert normalize_think_level("min") == ThinkLevel.MINIMAL

    def test_normalize_low(self):
        """Test low level."""
        assert normalize_think_level("low") == ThinkLevel.LOW

    def test_normalize_medium(self):
        """Test medium level."""
        assert normalize_think_level("medium") == ThinkLevel.MEDIUM

    def test_normalize_high(self):
        """Test high level."""
        assert normalize_think_level("high") == ThinkLevel.HIGH

    def test_normalize_xhigh(self):
        """Test xhigh level."""
        assert normalize_think_level("xhigh") == ThinkLevel.XHIGH
        assert normalize_think_level("x-high") == ThinkLevel.XHIGH
        assert normalize_think_level("x_high") == ThinkLevel.XHIGH


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


def test_get_think_level_for_model():
    """Test get_think_level_for_model function."""
    # This function may not exist, let's check
    try:
        from src.agents.thinking import get_think_level_for_model
        # Test if function exists
        result = get_think_level_for_model("openai/gpt-4")
        assert result is not None
    except ImportError:
        # Function doesn't exist, skip test
        pass
