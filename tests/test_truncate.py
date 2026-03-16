"""Tests for truncate utilities."""

import pytest

from src.utils.truncate import (
    truncate,
    truncate_with_count,
    truncate_json,
)


class TestTruncate:
    """Tests for truncate function."""

    def test_truncate_empty_string(self):
        """Test truncating empty string."""
        assert truncate("") == ""

    def test_truncate_none(self):
        """Test truncating None."""
        assert truncate(None) == ""

    def test_truncate_under_max_length(self):
        """Test string under max length returns unchanged."""
        assert truncate("hello", max_length=10) == "hello"

    def test_truncate_equal_to_max_length(self):
        """Test string equal to max length returns unchanged."""
        assert truncate("hello", max_length=5) == "hello"

    def test_truncate_over_max_length(self):
        """Test string over max length is truncated."""
        result = truncate("hello world", max_length=5)
        assert result == "hello..."

    def test_truncate_custom_suffix(self):
        """Test custom suffix."""
        result = truncate("hello world", max_length=5, suffix="…")
        assert result == "hello…"

    def test_truncate_non_string(self):
        """Test non-string input is converted to string."""
        assert truncate(12345, max_length=3) == "123..."

    def test_truncate_unicode(self):
        """Test unicode characters."""
        result = truncate("你好世界", max_length=2)
        assert result == "你好..."


class TestTruncateWithCount:
    """Tests for truncate_with_count function."""

    def test_truncate_with_count_empty(self):
        """Test empty string returns (empty)."""
        assert truncate_with_count("") == "(empty)"

    def test_truncate_with_count_none(self):
        """Test None returns (empty)."""
        assert truncate_with_count(None) == "(empty)"

    def test_truncate_with_count_under_max(self):
        """Test string under max length returns unchanged."""
        assert truncate_with_count("hello", max_length=10) == "hello"

    def test_truncate_with_count_over_max(self):
        """Test string over max length shows count."""
        result = truncate_with_count("hello world", max_length=5)
        assert result == "hello... [6 chars hidden]"

    def test_truncate_with_count_non_string(self):
        """Test non-string input."""
        assert truncate_with_count(12345, max_length=3) == "123... [2 chars hidden]"


class TestTruncateJson:
    """Tests for truncate_json function."""

    def test_truncate_json_dict(self):
        """Test truncating dictionary."""
        data = {"key": "value", "number": 123}
        result = truncate_json(data, max_length=50)
        assert "key" in result
        assert "value" in result

    def test_truncate_json_list(self):
        """Test truncating list."""
        data = [1, 2, 3, 4, 5]
        result = truncate_json(data, max_length=10)
        assert "1" in result

    def test_truncate_json_string(self):
        """Test truncating string input."""
        result = truncate_json("hello world", max_length=5)
        assert "hello" in result

    def test_truncate_json_nested(self):
        """Test truncating nested data."""
        data = {"outer": {"inner": "value", "list": [1, 2, 3]}}
        result = truncate_json(data, max_length=20)
        assert "outer" in result or "[" in result

    def test_truncate_json_empty(self):
        """Test truncating empty data."""
        result = truncate_json({}, max_length=10)
        assert result == "{}"
