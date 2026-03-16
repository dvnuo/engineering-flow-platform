"""Tests for error handling utilities."""

import pytest
import json

from src.agents.errors import (
    LLMError,
    HTTPError,
    NetworkError,
    AuthenticationError,
    handle_httpx_error,
    handle_httpx_request_error,
    extract_error_details,
    format_error_for_user,
    create_error_response,
)


class TestLLMError:
    """Tests for LLMError base class."""

    def test_llm_error_basic(self):
        """Test basic LLMError creation."""
        error = LLMError("Test error")
        assert error.message == "Test error"
        assert error.error_type == "llm_error"
        assert error.details == {}

    def test_llm_error_with_details(self):
        """Test LLMError with details."""
        error = LLMError(
            "Test error",
            error_type="test_type",
            details={"key": "value"},
            provider="openai",
            status_code=500,
        )
        assert error.message == "Test error"
        assert error.error_type == "test_type"
        assert error.details == {"key": "value"}
        assert error.provider == "openai"
        assert error.status_code == 500

    def test_llm_error_timestamp(self):
        """Test LLMError has timestamp."""
        error = LLMError("Test error")
        assert error.timestamp is not None
        assert "Z" in error.timestamp


class TestHTTPError:
    """Tests for HTTPError class."""

    def test_http_error_basic(self):
        """Test basic HTTPError creation."""
        error = HTTPError(500, "Internal Server Error", provider="openai")
        assert error.status_code == 500
        assert "500" in error.message
        assert error.provider == "openai"

    def test_http_error_401(self):
        """Test HTTP 401 error."""
        error = HTTPError(401, '{"error": "Unauthorized"}', provider="openai")
        assert error.status_code == 401
        assert "401" in error.message

    def test_http_error_429(self):
        """Test HTTP 429 rate limit error."""
        error = HTTPError(429, "Rate limit exceeded", provider="openai")
        assert error.error_type == "rate_limit"

    def test_http_error_500(self):
        """Test HTTP 500 server error."""
        error = HTTPError(500, "Internal Server Error", provider="openai")
        assert error.error_type == "server_error"

    def test_http_error_parse_json(self):
        """Test HTTPError parses JSON response."""
        body = json.dumps({"error": {"message": "Test error message"}})
        error = HTTPError(400, body, provider="openai")
        assert "Test error message" in error.message

    def test_http_error_empty_body(self):
        """Test HTTPError with empty body."""
        error = HTTPError(500, "", provider="openai")
        assert error.status_code == 500


class TestNetworkError:
    """Tests for NetworkError class."""

    def test_network_error_basic(self):
        """Test basic NetworkError creation."""
        error = NetworkError("Connection failed", provider="openai")
        assert error.message == "Connection failed"
        assert error.error_type == "network_error"
        assert error.provider == "openai"

    def test_network_error_with_original(self):
        """Test NetworkError with original exception."""
        original = ConnectionError("Original error")
        error = NetworkError("Connection failed", provider="openai", original_error=original)
        assert error.original_error == original


class TestAuthenticationError:
    """Tests for AuthenticationError class."""

    def test_auth_error_basic(self):
        """Test basic AuthenticationError creation."""
        error = AuthenticationError("Invalid API key", provider="openai")
        assert error.message == "Invalid API key"
        assert error.error_type == "authentication_error"
        assert error.status_code == 401


class TestExtractErrorDetails:
    """Tests for extract_error_details function."""

    def test_extract_llm_error(self):
        """Test extracting details from LLMError."""
        error = LLMError("Test", error_type="test", provider="openai")
        details = extract_error_details(error)
        assert details["error_type"] == "test"
        assert details["provider"] == "openai"

    def test_extract_generic_error(self):
        """Test extracting details from generic error."""
        error = ValueError("Test value error")
        details = extract_error_details(error)
        assert details["error_type"] == "unknown_error"
        assert details["message"] == "Test value error"


class TestFormatErrorForUser:
    """Tests for format_error_for_user function."""

    def test_format_auth_error(self):
        """Test formatting authentication error."""
        error = AuthenticationError("Invalid key")
        result = format_error_for_user(error)
        assert "authentication" in result.lower() or "API key" in result

    def test_format_rate_limit_error(self):
        """Test formatting rate limit error."""
        error = HTTPError(429, "Rate limit", provider="openai")
        result = format_error_for_user(error)
        assert "rate" in result.lower() or "wait" in result.lower()

    def test_format_server_error(self):
        """Test formatting server error."""
        error = HTTPError(500, "Server error", provider="openai")
        result = format_error_for_user(error)
        assert "unavailable" in result.lower() or "try again" in result.lower()


class TestCreateErrorResponse:
    """Tests for create_error_response function."""

    def test_create_error_response_basic(self):
        """Test basic error response creation."""
        error = LLMError("Test error")
        response = create_error_response(error)
        assert response["success"] is False
        assert "error" in response
        assert "timestamp" in response

    def test_create_error_response_has_type(self):
        """Test error response has error type."""
        error = LLMError("Test", error_type="test_type")
        response = create_error_response(error)
        assert response["error"]["type"] == "test_type"
