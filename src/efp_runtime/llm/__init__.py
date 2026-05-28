"""LLM normalization for EFP Runtime v2."""

from .adapter import DefaultLLMEventAdapter, LLMEventAdapter
from .errors import (
    ProviderContextOverflowError,
    ProviderError,
    ProviderFatalError,
    ProviderTransientError,
)
from .events import LLMEvent, LLMEventType
from .openai import (
    provider_request_to_openai_chat,
    provider_request_to_openai_responses,
    request_message_to_openai_chat_messages,
    request_message_to_openai_responses_input,
    request_part_to_openai_responses_content,
    request_tool_call_to_openai_chat_tool_call,
    request_tool_result_to_openai_chat_message,
    request_tool_schema_to_openai_responses_tool,
    request_tool_schema_to_openai_tool,
)
from .provider import OpenAICompatibleProvider, ProviderTransport, RecordingTransport

__all__ = [
    "DefaultLLMEventAdapter",
    "LLMEvent",
    "LLMEventAdapter",
    "ProviderContextOverflowError",
    "ProviderError",
    "ProviderFatalError",
    "LLMEventType",
    "OpenAICompatibleProvider",
    "ProviderTransientError",
    "ProviderTransport",
    "RecordingTransport",
    "provider_request_to_openai_chat",
    "provider_request_to_openai_responses",
    "request_message_to_openai_chat_messages",
    "request_message_to_openai_responses_input",
    "request_part_to_openai_responses_content",
    "request_tool_call_to_openai_chat_tool_call",
    "request_tool_result_to_openai_chat_message",
    "request_tool_schema_to_openai_responses_tool",
    "request_tool_schema_to_openai_tool",
]
