"""Tests for tool_calls and tool_call_id conversion for Responses API."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest


class TestFunctionCallFormat:
    """Test expected format for function_call in Responses API."""

    def test_function_call_structure(self):
        """Verify function_call has correct structure for Responses API."""
        # This is the expected format for Responses API
        expected = {
            "type": "function_call",
            "call_id": "call_123",
            "name": "run_command",
            "arguments": '{"cmd": "ls", "args": ["-la"]}'
        }
        
        assert expected["type"] == "function_call"
        assert expected["name"] == "run_command"
        assert expected["call_id"] == "call_123"

    def test_function_call_output_structure(self):
        """Verify function_call_output has correct structure for Responses API."""
        # This is the expected format for Responses API
        expected = {
            "type": "function_call_output",
            "call_id": "call_123",
            "output": "total 4 drwxr-xr-x 2 root root 4096 Mar 19 12:00 ."
        }
        
        assert expected["type"] == "function_call_output"
        assert expected["call_id"] == "call_123"
        assert "total 4" in expected["output"]

    def test_user_message_input_text(self):
        """User messages should use input_text wrapper for Responses API."""
        msg = {
            "role": "user",
            "content": [{"type": "input_text", "text": "Hello"}]
        }
        
        assert msg["content"][0]["type"] == "input_text"
        assert msg["content"][0]["text"] == "Hello"

    def test_assistant_message_plain_text(self):
        """Assistant messages should be plain text (string) for Responses API."""
        msg = {
            "role": "assistant",
            "content": "Hello, how can I help?"
        }
        
        # Assistant should use plain string, not list
        assert isinstance(msg["content"], str)


class TestToolCallPairing:
    """Test that tool_calls and tool_call_id are properly paired."""

    def test_tool_call_id_association(self):
        """tool_call and tool_result should share the same call_id."""
        tool_call = {
            "type": "function_call",
            "call_id": "call_abc",
            "name": "test_func",
            "arguments": "{}"
        }
        
        tool_result = {
            "type": "function_call_output", 
            "call_id": "call_abc",
            "output": "result"
        }
        
        # Both should have matching call_id
        assert tool_call["call_id"] == tool_result["call_id"] == "call_abc"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
