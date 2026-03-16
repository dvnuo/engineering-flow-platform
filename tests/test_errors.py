"""Tests for error handling utilities."""

import json
from unittest.mock import MagicMock

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
    log_error,
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

    def test_http_error_parse_json_nested(self):
        """Test HTTPError parses nested JSON."""
        body = json.dumps({"error_message": "Nested error"})
        error = HTTPError(400, body, provider="openai")
        assert "Nested error" in error.message

    def test_http_error_parse_detail(self):
        """Test HTTPError parses detail field."""
        body = json.dumps({"detail": "Detail error"})
        error = HTTPError(400, body, provider="openai")
        assert "Detail error" in error.message

    def test_http_error_parse_details(self):
        """Test HTTPError parses details field."""
        body = json.dumps({"details": "Details error"})
        error = HTTPError(400, body, provider="openai")
        assert "Details error" in error.message

    def test_http_error_parse_non_dict_json(self):
        """Test HTTPError parses non-dict JSON."""
        body = json.dumps(["error1", "error2"])
        error = HTTPError(400, body, provider="openai")
        assert "error1" in error.message or "error2" in error.message

    def test_http_error_400(self):
        """Test HTTP 400 bad request."""
        error = HTTPError(400, "Bad Request", provider="openai")
        assert error.error_type == "bad_request"

    def test_http_error_403(self):
        """Test HTTP 403 forbidden."""
        error = HTTPError(403, "Forbidden", provider="openai")
        assert error.error_type == "bad_request"

    def test_http_error_404(self):
        """Test HTTP 404 not found."""
        error = HTTPError(404, "Not Found", provider="openai")
        assert error.error_type == "bad_request"

    def test_http_error_502(self):
        """Test HTTP 502 bad gateway."""
        error = HTTPError(502, "Bad Gateway", provider="openai")
        assert error.error_type == "server_error"

    def test_http_error_503(self):
        """Test HTTP 503 service unavailable."""
        error = HTTPError(503, "Service Unavailable", provider="openai")
        assert error.error_type == "server_error"

    def test_http_error_504(self):
        """Test HTTP 504 gateway timeout."""
        error = HTTPError(504, "Gateway Timeout", provider="openai")
        assert error.error_type == "server_error"

    def test_http_error_redirect(self):
        """Test HTTP redirect."""
        error = HTTPError(301, "Moved Permanently", provider="openai")
        assert error.error_type == "redirect"


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

    def test_extract_http_error(self):
        """Test extracting details from HTTPError."""
        error = HTTPError(500, "Server error", provider="openai")
        details = extract_error_details(error)
        assert details["status_code"] == 500

    def test_extract_network_error(self):
        """Test extracting details from NetworkError."""
        error = NetworkError("Connection failed", provider="openai")
        details = extract_error_details(error)
        assert details["provider"] == "openai"


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

    def test_format_bad_request(self):
        """Test formatting bad request error."""
        error = HTTPError(400, "Bad request", provider="openai")
        result = format_error_for_user(error)
        assert "request failed" in result.lower()

    def test_format_generic_error(self):
        """Test formatting generic error."""
        error = Exception("Generic error")
        result = format_error_for_user(error)
        assert "error" in result.lower()


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


class TestLogError:
    """Tests for log_error function."""

    def test_log_error_basic(self):
        """Test basic log_error call."""
        error = LLMError("Test error")
        log_error(error, component="TEST", level="ERROR")

    def test_log_error_warning_level(self):
        """Test log_error with WARNING level."""
        error = LLMError("Test warning")
        log_error(error, component="TEST", level="WARNING")


class TestHandleHttpxErrors:
    """Tests for httpx error handlers."""

    def test_handle_httpx_error_with_response(self):
        """Test handle_httpx_error with response."""
        import httpx
        mock_request = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.text = '{"error": "test"}'
        error = httpx.HTTPStatusError("error", request=mock_request, response=mock_response)
        
        result = handle_httpx_error(error, provider="test")
        assert result.status_code == 400

    def test_handle_httpx_error_without_response(self):
        """Test handle_httpx_error without response."""
        import httpx
        mock_request = MagicMock()
        error = httpx.HTTPStatusError("error", request=mock_request, response=None)
        
        result = handle_httpx_error(error, provider="test")
        assert result.status_code == 0

    def test_handle_httpx_request_error_connect_error(self):
        """Test handle_httpx_request_error with ConnectError."""
        import httpx
        error = httpx.ConnectError("connection failed")
        
        result = handle_httpx_request_error(error, provider="test")
        assert "Connection failed" in result.message
        assert result.provider == "test"

    def test_handle_httpx_request_error_timeout(self):
        """Test handle_httpx_request_error with TimeoutException."""
        import httpx
        error = httpx.TimeoutException("timed out")
        
        result = handle_httpx_request_error(error, provider="test")
        assert "timed out" in result.message or "timeout" in result.message.lower()

    def test_handle_httpx_request_error_read_error(self):
        """Test handle_httpx_request_error with ReadError."""
        import httpx
        error = httpx.ReadError("read failed")
        
        result = handle_httpx_request_error(error, provider="test")
        assert "read" in result.message.lower()

    def test_handle_httpx_request_error_write_error(self):
        """Test handle_httpx_request_error with WriteError."""
        import httpx
        error = httpx.WriteError("write failed")
        
        result = handle_httpx_request_error(error, provider="test")
        assert "send" in result.message.lower()

    def test_handle_httpx_request_error_protocol_error(self):
        """Test handle_httpx_request_error with RemoteProtocolError."""
        import httpx
        error = httpx.RemoteProtocolError("protocol error")
        
        result = handle_httpx_request_error(error, provider="test")
        assert "protocol" in result.message.lower()

    def test_handle_httpx_request_error_generic(self):
        """Test handle_httpx_request_error with generic error."""
        import httpx
        error = httpx.RequestError("generic error")
        
        result = handle_httpx_request_error(error, provider="test")
        assert result.error_type == "network_error"
