"""
Error handling utilities for LLM providers.

Provides consistent error handling, logging, and user-friendly error messages.
"""

import datetime
import json
import logging
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)


class LLMError(Exception):
    """Base exception for LLM errors."""
    
    def __init__(
        self,
        message: str,
        error_type: str = "llm_error",
        details: Optional[Dict] = None,
        provider: Optional[str] = None,
        status_code: Optional[int] = None,
        original_error: Optional[Exception] = None,
    ):
        self.message = message
        self.error_type = error_type
        self.details = details or {}
        self.provider = provider
        self.status_code = status_code
        self.original_error = original_error
        self.timestamp = datetime.datetime.utcnow().isoformat() + "Z"
        
        super().__init__(self.message)


class HTTPError(LLMError):
    """HTTP-related errors from LLM providers."""
    
    def __init__(
        self,
        status_code: int,
        response_body: str,
        provider: str = "unknown",
        endpoint: str = "",
        request_body: Optional[Dict] = None,
    ):
        self.status_code = status_code
        self.response_body = response_body
        
        # Parse error message from response
        error_message = self._parse_error_message(status_code, response_body)
        
        super().__init__(
            message=f"HTTP {status_code}: {error_message}",
            error_type=self._get_error_type(status_code),
            details={
                "status_code": status_code,
                "response_body": response_body[:1000] if response_body else "",
                "endpoint": endpoint,
                "provider": provider,
            },
            provider=provider,
            status_code=status_code,
        )
    
    def _parse_error_message(self, status_code: int, body: str) -> str:
        """Parse error message from HTTP response body."""
        if not body:
            return self._get_default_message(status_code)
        
        # Try to parse as JSON
        try:
            data = json.loads(body)
            if isinstance(data, dict):
                # Common error formats
                for key in ["error", "message", "error_message", "detail", "details"]:
                    if key in data:
                        msg = data[key]
                        if isinstance(msg, dict):
                            msg = msg.get("message", str(msg))
                        return str(msg)
                return str(data)
        except json.JSONDecodeError:
            pass
        
        # Return first 200 chars of body
        return body[:200] if len(body) > 200 else body
    
    def _get_default_message(self, status_code: int) -> str:
        """Get default message for HTTP status code."""
        messages = {
            400: "Bad request - check your input",
            401: "Authentication failed - check your API key",
            403: "Permission denied - check your access rights",
            404: "Resource not found",
            429: "Rate limit exceeded - slow down",
            500: "Internal server error - try again later",
            502: "Bad gateway - service temporarily unavailable",
            503: "Service unavailable - try again later",
            504: "Gateway timeout - try again later",
        }
        return messages.get(status_code, f"HTTP {status_code}")
    
    def _get_error_type(self, status_code: int) -> str:
        """Get error type based on status code."""
        if status_code >= 500:
            return "server_error"
        elif status_code == 429:
            return "rate_limit"
        elif status_code >= 400:
            return "bad_request"
        elif status_code >= 300:
            return "redirect"
        else:
            return "http_error"


class NetworkError(LLMError):
    """Network-related errors (timeouts, DNS, connection failures)."""
    
    def __init__(
        self,
        message: str,
        provider: str = "unknown",
        endpoint: str = "",
        original_error: Optional[Exception] = None,
    ):
        super().__init__(
            message=message,
            error_type="network_error",
            details={
                "endpoint": endpoint,
                "provider": provider,
                "error_type": type(original_error).__name__ if original_error else "unknown",
            },
            provider=provider,
        )
        self.original_error = original_error


class AuthenticationError(LLMError):
    """Authentication/authorization errors."""
    
    def __init__(
        self,
        message: str,
        provider: str = "unknown",
        details: Optional[Dict] = None,
    ):
        super().__init__(
            message=message,
            error_type="authentication_error",
            details=details or {},
            provider=provider,
            status_code=401,
        )


def handle_httpx_error(
    error: httpx.HTTPStatusError,
    provider: str = "unknown",
    endpoint: str = "",
) -> HTTPError:
    """Convert httpx.HTTPStatusError to our custom HTTPError."""
    status_code = error.response.status_code if error.response else 0
    response_body = ""
    
    if error.response:
        try:
            response_body = error.response.text
        except Exception:
            response_body = ""
    
    return HTTPError(
        status_code=status_code,
        response_body=response_body,
        provider=provider,
        endpoint=endpoint,
        request_body=None,
    )


def handle_httpx_request_error(
    error: httpx.RequestError,
    provider: str = "unknown",
    endpoint: str = "",
) -> NetworkError:
    """Convert httpx.RequestError to NetworkError."""
    message = str(error)
    
    # Provide more helpful messages
    if isinstance(error, httpx.ConnectError):
        message = f"Connection failed to {provider}. Check your network and API endpoint."
    elif isinstance(error, httpx.TimeoutException):
        message = f"Request timed out. {provider} may be slow or unavailable."
    elif isinstance(error, httpx.ReadError):
        message = f"Failed to read response from {provider}."
    elif isinstance(error, httpx.WriteError):
        message = f"Failed to send request to {provider}."
    elif isinstance(error, httpx.RemoteProtocolError):
        message = f"Protocol error with {provider}. The connection was interrupted."
    
    return NetworkError(
        message=message,
        provider=provider,
        endpoint=endpoint,
        original_error=error,
    )


def extract_error_details(error: Exception) -> Dict[str, Any]:
    """Extract structured error details from any exception."""
    if isinstance(error, LLMError):
        return {
            "error_type": error.error_type,
            "message": error.message,
            "provider": error.provider,
            "status_code": error.status_code,
            "timestamp": error.timestamp,
            "details": error.details,
        }
    
    # Generic exception
    return {
        "error_type": "unknown_error",
        "message": str(error),
        "provider": None,
        "status_code": None,
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        "details": {
            "exception_type": type(error).__name__,
        },
    }


def format_error_for_user(error: Exception) -> str:
    """Format error message for display to users."""
    if isinstance(error, LLMError):
        # Provide user-friendly messages
        if error.error_type == "authentication_error":
            return "Authentication failed. Please check your API key and credentials."
        elif error.error_type == "rate_limit":
            return "Rate limit exceeded. Please wait a moment and try again."
        elif error.error_type == "server_error":
            return "The AI service is temporarily unavailable. Please try again later."
        elif error.error_type == "bad_request":
            return f"Request failed: {error.message}"
        else:
            return error.message
    
    # Generic error
    return f"An error occurred: {str(error)}"


def log_error(
    error: Exception,
    component: str = "LLM",
    level: str = "ERROR",
) -> None:
    """Log error with full details."""
    error_details = extract_error_details(error)
    
    # Log at appropriate level
    if level == "ERROR":
        logger.error(f"[{component}] {error_details['message']}")
        logger.error(f"[{component}] Error details: {json.dumps(error_details, indent=2)}")
    elif level == "WARNING":
        logger.warning(f"[{component}] {error_details['message']}")
        logger.warning(f"[{component}] Error details: {json.dumps(error_details, indent=2)}")
    else:
        logger.debug(f"[{component}] {error_details['message']}")


def create_error_response(error: Exception) -> Dict[str, Any]:
    """Create a structured error response for APIs."""
    error_details = extract_error_details(error)
    
    return {
        "success": False,
        "error": {
            "type": error_details.get("error_type", "unknown"),
            "message": format_error_for_user(error),
            "details": error_details,
        },
        "timestamp": error_details.get("timestamp"),
    }


def wrap_provider_call(
    provider_name: str,
    endpoint: str,
    func,
):
    """Decorator to wrap provider calls with error handling."""
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except httpx.HTTPStatusError as e:
            error = handle_httpx_error(e, provider=provider_name, endpoint=endpoint)
            log_error(error, component=provider_name.upper())
            raise error
        except httpx.RequestError as e:
            error = handle_httpx_request_error(e, provider=provider_name, endpoint=endpoint)
            log_error(error, component=provider_name.upper())
            raise error
        except Exception as e:
            logger.exception(f"[{provider_name}] Unexpected error: {e}")
            raise LLMError(
                message=str(e),
                error_type="unexpected_error",
                details={"exception_type": type(e).__name__},
                provider=provider_name,
                original_error=e,
            )
    
    return wrapper
