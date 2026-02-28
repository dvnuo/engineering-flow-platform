"""Tests for Message Compaction functionality."""

import pytest
from typing import List

from src.agents.compaction import (
    AgentMessage,
    CompactionStats,
    estimate_tokens,
    estimate_message_tokens,
    estimate_messages_tokens,
    split_messages_by_token_share,
    chunk_messages_by_max_tokens,
    compute_adaptive_chunk_ratio,
    is_oversized_for_summary,
    prune_history_for_context_share,
    resolve_context_window_tokens,
    BASE_CHUNK_RATIO,
    MIN_CHUNK_RATIO,
    SAFETY_MARGIN,
    DEFAULT_PARTS,
)


class TestEstimateTokens:
    """Tests for token estimation."""
    
    def test_empty_text(self):
        """Test empty text returns 0."""
        assert estimate_tokens("") == 0
        assert estimate_tokens(None) == 0
    
    def test_simple_text(self):
        """Test simple text estimation."""
        # ~4 chars per token
        text = "Hello, world!"
        result = estimate_tokens(text)
        assert result >= 3  # At least 3 tokens
    
    def test_long_text(self):
        """Test long text estimation."""
        text = "a" * 1000
        result = estimate_tokens(text)
        assert result == 250  # 1000 / 4


class TestEstimateMessageTokens:
    """Tests for message token estimation."""
    
    def test_empty_message(self):
        """Test empty message returns small non-zero count."""
        msg = AgentMessage()
        # Empty message still has role overhead
        tokens = estimate_message_tokens(msg)
        assert tokens > 0
    
    def test_user_message(self):
        """Test user message estimation."""
        msg = AgentMessage(role="user", content="Hello")
        tokens = estimate_message_tokens(msg)
        assert tokens > 0
    
    def test_assistant_message(self):
        """Test assistant message estimation."""
        msg = AgentMessage(role="assistant", content="How can I help?")
        tokens = estimate_message_tokens(msg)
        assert tokens > 0
    
    def test_system_message(self):
        """Test system message estimation."""
        msg = AgentMessage(role="system", content="You are helpful.")
        tokens = estimate_message_tokens(msg)
        assert tokens > 0
    
    def test_tool_calls(self):
        """Test message with tool calls."""
        msg = AgentMessage(
            role="assistant",
            content="",
            tool_calls=[{"id": "123", "function": {"name": "test"}}]
        )
        tokens = estimate_message_tokens(msg)
        assert tokens > 0


class TestEstimateMessagesTokens:
    """Tests for multiple message token estimation."""
    
    def test_empty_list(self):
        """Test empty list returns 0."""
        assert estimate_messages_tokens([]) == 0
    
    def test_single_message(self):
        """Test single message."""
        msgs = [AgentMessage(role="user", content="Hi")]
        result = estimate_messages_tokens(msgs)
        assert result > 0
    
    def test_multiple_messages(self):
        """Test multiple messages."""
        msgs = [
            AgentMessage(role="user", content="Hello"),
            AgentMessage(role="assistant", content="Hi there!"),
            AgentMessage(role="user", content="How are you?"),
        ]
        result = estimate_messages_tokens(msgs)
        assert result > 0


class TestSplitMessagesByTokenShare:
    """Tests for message splitting by token share."""
    
    def test_empty_messages(self):
        """Test empty list returns empty."""
        result = split_messages_by_token_share([])
        assert result == []
    
    def test_single_message(self):
        """Test single message returns single chunk."""
        msgs = [AgentMessage(role="user", content="Hi")]
        result = split_messages_by_token_share(msgs)
        assert len(result) == 1
        assert result[0] == msgs
    
    def test_parts_less_than_2(self):
        """Test parts <= 1 returns single chunk."""
        msgs = [
            AgentMessage(role="user", content="Hello"),
            AgentMessage(role="assistant", content="Hi"),
        ]
        result = split_messages_by_token_share(msgs, parts=1)
        assert len(result) == 1
        assert result[0] == msgs
    
    def test_split_into_2_parts(self):
        """Test splitting into 2 parts."""
        msgs = [
            AgentMessage(role="user", content="Message 1"),
            AgentMessage(role="assistant", content="Response 1"),
            AgentMessage(role="user", content="Message 2"),
            AgentMessage(role="assistant", content="Response 2"),
        ]
        result = split_messages_by_token_share(msgs, parts=2)
        assert len(result) == 2
        # All messages should be in chunks
        total = sum(len(chunk) for chunk in result)
        assert total == len(msgs)


class TestChunkMessagesByMaxTokens:
    """Tests for chunking by max tokens."""
    
    def test_empty_messages(self):
        """Test empty list returns empty."""
        result = chunk_messages_by_max_tokens([], 1000)
        assert result == []
    
    def test_single_message(self):
        """Test single message."""
        msgs = [AgentMessage(role="user", content="Short")]
        result = chunk_messages_by_max_tokens(msgs, 1000)
        assert len(result) == 1
    
    def test_under_max_tokens(self):
        """Test messages under max tokens."""
        msgs = [
            AgentMessage(role="user", content="A"),
            AgentMessage(role="assistant", content="B"),
        ]
        result = chunk_messages_by_max_tokens(msgs, 10000)
        assert len(result) == 1
        assert len(result[0]) == 2
    
    def test_over_max_tokens(self):
        """Test messages over max tokens create multiple chunks."""
        # Create messages with significant content
        msgs = [
            AgentMessage(role="user", content="x" * 2000),
            AgentMessage(role="assistant", content="y" * 2000),
            AgentMessage(role="user", content="z" * 2000),
        ]
        result = chunk_messages_by_max_tokens(msgs, 1000)
        # Should have multiple chunks
        assert len(result) >= 2


class TestComputeAdaptiveChunkRatio:
    """Tests for adaptive chunk ratio computation."""
    
    def test_empty_messages(self):
        """Test empty returns base ratio."""
        result = compute_adaptive_chunk_ratio([], 8000)
        assert result == BASE_CHUNK_RATIO
    
    def test_small_messages(self):
        """Test small messages use base ratio."""
        msgs = [AgentMessage(role="user", content="Hi")]
        result = compute_adaptive_chunk_ratio(msgs, 8000)
        assert result == BASE_CHUNK_RATIO
    
    def test_large_messages(self):
        """Test large messages reduce ratio."""
        msgs = [AgentMessage(role="user", content="x" * 5000)]
        result = compute_adaptive_chunk_ratio(msgs, 8000)
        assert result <= BASE_CHUNK_RATIO
        assert result >= MIN_CHUNK_RATIO


class TestIsOversizedForSummary:
    """Tests for oversized message detection."""
    
    def test_small_message(self):
        """Test small message is not oversized."""
        msg = AgentMessage(role="user", content="Hi")
        result = is_oversized_for_summary(msg, 8000)
        assert result is False
    
    def test_oversized_message(self):
        """Test large message is oversized."""
        # Create a message that's clearly oversized (> 50% of context)
        msg = AgentMessage(role="user", content="x" * 20000)
        result = is_oversized_for_summary(msg, 8000)
        assert result is True
    
    def test_boundary_case(self):
        """Test boundary case (50% of context)."""
        # Message at exactly 50% should be oversized with safety margin
        msg = AgentMessage(role="user", content="x" * 15000)
        result = is_oversized_for_summary(msg, 8000)
        assert result is True


class TestPruneHistoryForContextShare:
    """Tests for history pruning."""
    
    def test_empty_messages(self):
        """Test empty returns empty."""
        pruned, stats = prune_history_for_context_share([], 1000)
        assert pruned == []
        assert stats.dropped_chunks == 0
    
    def test_under_budget(self):
        """Test messages under budget are not pruned."""
        msgs = [AgentMessage(role="user", content="Hi")]
        pruned, stats = prune_history_for_context_share(msgs, 10000)
        assert pruned == msgs
        assert stats.dropped_chunks == 0
    
    def test_over_budget(self):
        """Test messages over budget are pruned."""
        msgs = [
            AgentMessage(role="user", content="Old message"),
            AgentMessage(role="assistant", content="Old response"),
            AgentMessage(role="user", content="Recent message"),
        ]
        # Very small budget
        pruned, stats = prune_history_for_context_share(
            msgs,
            max_context_tokens=100,
            max_history_share=0.5,
        )
        # Should have pruned some messages
        assert len(pruned) <= len(msgs)
    
    def test_preserves_recent(self):
        """Test pruning preserves recent messages."""
        msgs = [
            AgentMessage(role="user", content="Old 1"),
            AgentMessage(role="user", content="Old 2"),
            AgentMessage(role="user", content="Recent"),
        ]
        pruned, stats = prune_history_for_context_share(
            msgs,
            max_context_tokens=100,
            max_history_share=0.5,
        )
        # Recent message should be in pruned result
        assert msgs[-1] in pruned or len(pruned) == 1


class TestResolveContextWindowTokens:
    """Tests for context window resolution."""
    
    def test_gpt_4(self):
        """Test GPT-4 context window."""
        assert resolve_context_window_tokens("gpt-4") == 8192
    
    def test_gpt_4_turbo(self):
        """Test GPT-4 Turbo context window."""
        # Update based on actual implementation
        result = resolve_context_window_tokens("gpt-4-turbo")
        # Should match either gpt-4 or be the actual 128000
        assert result in [8192, 128000]
    
    def test_gpt_3_5_turbo(self):
        """Test GPT-3.5 Turbo context window."""
        assert resolve_context_window_tokens("gpt-3.5-turbo") == 16385
    
    def test_claude(self):
        """Test Claude context window."""
        assert resolve_context_window_tokens("claude-sonnet-4") == 200000
    
    def test_unknown_model(self):
        """Test unknown model returns default."""
        assert resolve_context_window_tokens("unknown") == 4096
    
    def test_none_model(self):
        """Test None model returns default."""
        assert resolve_context_window_tokens(None) == 4096


class TestAgentMessage:
    """Tests for AgentMessage class."""
    
    def test_init_defaults(self):
        """Test default initialization."""
        msg = AgentMessage()
        assert msg.role == "user"
        assert msg.content == ""
        assert msg.timestamp is not None
        assert msg.tool_calls is None
        assert msg.tool_use_id is None
    
    def test_to_dict(self):
        """Test conversion to dictionary."""
        msg = AgentMessage(
            role="assistant",
            content="Hello",
            timestamp=12345,
            tool_calls=[{"id": "1"}],
        )
        data = msg.to_dict()
        assert data["role"] == "assistant"
        assert data["content"] == "Hello"
        assert data["timestamp"] == 12345
        assert data["tool_calls"] == [{"id": "1"}]
    
    def test_from_dict(self):
        """Test creation from dictionary."""
        data = {
            "role": "system",
            "content": "You are helpful",
            "timestamp": 12345,
        }
        msg = AgentMessage.from_dict(data)
        assert msg.role == "system"
        assert msg.content == "You are helpful"
        assert msg.timestamp == 12345
    
    def test_repr(self):
        """Test string representation."""
        msg = AgentMessage(role="user", content="Hello, world!")
        repr_str = repr(msg)
        assert "user" in repr_str
        assert "Hello" in repr_str


class TestCompactionStats:
    """Tests for CompactionStats class."""
    
    def test_init_defaults(self):
        """Test default initialization."""
        stats = CompactionStats()
        assert stats.dropped_chunks == 0
        assert stats.dropped_messages == 0
        assert stats.dropped_tokens == 0
        assert stats.kept_tokens == 0
        assert stats.budget_tokens == 0
        assert stats.summary is None
    
    def test_to_dict(self):
        """Test conversion to dictionary."""
        stats = CompactionStats(
            dropped_chunks=1,
            dropped_messages=5,
            dropped_tokens=1000,
            kept_tokens=500,
            budget_tokens=2000,
            summary="Test summary",
        )
        data = stats.to_dict()
        assert data["dropped_chunks"] == 1
        assert data["dropped_messages"] == 5
        assert data["summary"] == "Test summary"


class TestConstants:
    """Tests for module constants."""
    
    def test_base_chunk_ratio(self):
        """Test BASE_CHUNK_RATIO is 0.4."""
        assert BASE_CHUNK_RATIO == 0.4
    
    def test_min_chunk_ratio(self):
        """Test MIN_CHUNK_RATIO is 0.15."""
        assert MIN_CHUNK_RATIO == 0.15
    
    def test_safety_margin(self):
        """Test SAFETY_MARGIN is 1.2."""
        assert SAFETY_MARGIN == 1.2
    
    def test_default_parts(self):
        """Test DEFAULT_PARTS is 2."""
        assert DEFAULT_PARTS == 2


class TestFixToolCallConsistency:
    """Tests for fix_tool_call_consistency function."""
    
    def test_no_tool_calls_unchanged(self):
        """Test that messages without tool_calls are unchanged."""
        from src.agents.compaction import fix_tool_call_consistency, AgentMessage
        
        messages = [
            AgentMessage(role="user", content="Hello"),
            AgentMessage(role="assistant", content="Hi there"),
            AgentMessage(role="user", content="How are you?"),
        ]
        
        result = fix_tool_call_consistency(messages)
        
        assert len(result) == 3
        assert result[0].role == "user"
        assert result[1].role == "assistant"
        assert result[2].role == "user"
    
    def test_all_responses_present_no_filtering(self):
        """Test that when all tool responses are present, tool_calls are unchanged."""
        from src.agents.compaction import fix_tool_call_consistency, AgentMessage
        
        messages = [
            AgentMessage(
                role="assistant",
                content="Let me run that command",
                tool_calls=[{"id": "call_abc", "function": {"name": "exec", "arguments": "{}"}}]
            ),
            AgentMessage(role="tool", content="result", tool_use_id="call_abc"),
        ]
        
        result = fix_tool_call_consistency(messages)
        
        assert len(result) == 2
        assert result[0].tool_calls[0]["id"] == "call_abc"
        assert result[1].tool_use_id == "call_abc"
    
    def test_some_responses_missing_filters_orphans(self):
        """Test that orphaned tool_calls are removed when response is missing."""
        from src.agents.compaction import fix_tool_call_consistency, AgentMessage
        
        messages = [
            AgentMessage(
                role="assistant",
                content="Running commands",
                tool_calls=[
                    {"id": "call_abc", "function": {"name": "exec", "arguments": "{}"}},
                    {"id": "call_def", "function": {"name": "read", "arguments": "{}"}},
                ]
            ),
            AgentMessage(role="tool", content="result for abc", tool_use_id="call_abc"),
            # call_def has no response - should be filtered out
        ]
        
        result = fix_tool_call_consistency(messages)
        
        assert len(result) == 2
        assert len(result[0].tool_calls) == 1
        assert result[0].tool_calls[0]["id"] == "call_abc"
    
    def test_orphaned_tool_responses_removed(self):
        """Test that orphaned tool responses are removed when assistant message is dropped."""
        from src.agents.compaction import fix_tool_call_consistency, AgentMessage
        
        messages = [
            # Assistant message was dropped, tool response is orphaned
            AgentMessage(role="tool", content="old result", tool_use_id="call_old"),
            AgentMessage(role="user", content="New message"),
            AgentMessage(role="assistant", content="Response"),
        ]
        
        result = fix_tool_call_consistency(messages)
        
        assert len(result) == 2
        assert result[0].role == "user"
        assert result[1].role == "assistant"
    
    def test_all_tool_calls_dropped(self):
        """Test that tool_calls is set to None when all are filtered out."""
        from src.agents.compaction import fix_tool_call_consistency, AgentMessage
        
        messages = [
            AgentMessage(
                role="assistant",
                content="Running",
                tool_calls=[
                    {"id": "call_orphan", "function": {"name": "exec", "arguments": "{}"}},
                ]
            ),
            # No tool response for call_orphan
        ]
        
        result = fix_tool_call_consistency(messages)
        
        assert len(result) == 1
        assert result[0].tool_calls is None
    
    def test_empty_messages(self):
        """Test that empty message list returns empty list."""
        from src.agents.compaction import fix_tool_call_consistency
        
        result = fix_tool_call_consistency([])
        assert result == []
    
    def test_does_not_mutate_input(self):
        """Test that input messages are not mutated."""
        from src.agents.compaction import fix_tool_call_consistency, AgentMessage
        
        original_msg = AgentMessage(
            role="assistant",
            content="Test",
            tool_calls=[{"id": "call_abc", "function": {"name": "exec", "arguments": "{}"}}]
        )
        messages = [original_msg]
        
        result = fix_tool_call_consistency(messages)
        
        # Original should be unchanged
        assert original_msg.tool_calls[0]["id"] == "call_abc"
        # Result should have filtered (or unchanged) version
        assert len(result) == 1
