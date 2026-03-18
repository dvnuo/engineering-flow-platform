"""
LLM client for Engineering Flow Platform - Supports multiple providers.

Providers:
- OpenAI (GPT-3.5, GPT-4)
- GitHub Copilot
- Claude (Anthropic)
- Ollama (Local)

Debug Logging:
- Enable with log_level: DEBUG in config.yaml
- Logs: LLM requests, responses, reasoning, tool calls
- Complete input/output when debug is enabled (no truncation)
"""

import asyncio
import json
import logging
import os
from typing import Any, Dict, List, Optional

import httpx

from src.config import config
from src.utils.truncate import truncate, truncate_with_count
from src.sessions.usage import usage_tracker, estimate_cost
from .errors import (
    handle_httpx_error,
    handle_httpx_request_error,
    HTTPError,
    NetworkError,
    LLMError,
    log_error,
    format_error_for_user,
    create_error_response,
)

logger = logging.getLogger(__name__)

# Debug logging cache
_DEBUG_ENABLED = None
_HTTPX_TRACE_ENABLED = None

# Debug logging is enabled when logger.level is DEBUG
# Set log_level: DEBUG in config.yaml or use --debug flag
# When DEBUG, complete input/output is logged (no truncation)

def _setup_httpx_logging():
    """Configure httpx logging based on debug settings."""
    global _HTTPX_TRACE_ENABLED
    
    if _HTTPX_TRACE_ENABLED is not None:
        return  # Already configured
    
    try:
        # Check if httpx trace logging should be enabled
        debug_config = config.debug() if hasattr(config, 'debug') else {}
        httpx_trace = debug_config.get('httpx_trace', False) if debug_config else False
        
        # Get httpx logger
        httpx_logger = logging.getLogger("httpx")
        
        if httpx_trace:
            # Enable DEBUG level for httpx to show trace logs
            httpx_logger.setLevel(logging.DEBUG)
            _HTTPX_TRACE_ENABLED = True
        else:
            # Disable httpx trace logs by default
            # Set to WARNING to suppress DEBUG trace logs
            httpx_logger.setLevel(logging.WARNING)
            _HTTPX_TRACE_ENABLED = False
        
        # Also configure httpcore (underlying HTTP library)
        httpcore_logger = logging.getLogger("httpcore")
        httpcore_logger.setLevel(httpx_logger.level)
        
    except Exception:
        _HTTPX_TRACE_ENABLED = False


def _is_debug_enabled() -> bool:
    """Check if debug mode is enabled (logger is DEBUG level)."""
    global _DEBUG_ENABLED
    if _DEBUG_ENABLED is None:
        _DEBUG_ENABLED = logger.isEnabledFor(logging.DEBUG)
        # Setup httpx logging after first check
        _setup_httpx_logging()
    return _DEBUG_ENABLED


def _is_httpx_trace_enabled() -> bool:
    """Check if httpx trace logging is enabled."""
    global _HTTPX_TRACE_ENABLED
    if _HTTPX_TRACE_ENABLED is None:
        _setup_httpx_logging()
    return _HTTPX_TRACE_ENABLED


def _is_full_output() -> bool:
    """Check if full output mode is enabled.
    Always true when debug is enabled - we want complete logs for debugging."""
    return _is_debug_enabled()


def _log_request(component: str, url: str, method: str = "POST", headers: Dict = None, payload: Dict = None):
    """Log request with consistent format. Only formats if debug is enabled."""
    if not _is_debug_enabled():
        return
    logger.debug(f"=== [{component}] REQUEST ===")
    logger.debug(f"Method: {method}")
    logger.debug(f"URL: {url}")
    if headers:
        logger.debug(f"Headers: {json.dumps(_sanitize_headers(headers))}")
    if payload:
        logger.debug(f"Payload: {json.dumps(payload, indent=2, default=str)}")


def _log_response(component: str, status: int, body: Any = None):
    """Log response with consistent format. Only logs if debug is enabled."""
    if not _is_debug_enabled():
        return
    logger.debug(f"=== [{component}] RESPONSE ===")
    logger.debug(f"Status: {status}")
    if body:
        body_str = json.dumps(body, indent=2, default=str) if isinstance(body, (dict, list)) else str(body)
        logger.debug(f"Body: {body_str}")


def _sanitize_headers(headers: Dict[str, str]) -> Dict[str, str]:
    """Remove sensitive headers for logging."""
    sanitized = {}
    for k, v in headers.items():
        if "authorization" in k.lower() or "api-key" in k.lower() or "token" in k.lower():
            sanitized[k] = f"[REDACTED:{len(v)} chars]"
        else:
            sanitized[k] = v
    return sanitized


def _truncate_text(text: str, max_length: int = 200) -> str:
    """Truncate text for logging preview (wrapper for truncate_with_count)."""
    return truncate_with_count(text, max_length)


def _convert_messages_to_input_items(messages: List[Dict]) -> List[Dict]:
    """Convert Chat-style messages to Responses API input_items format."""
    items = []
    for msg in messages:
        role = msg.get("role", "user")
        if role == "tool":
            continue
        content = msg.get("content", "")
        if isinstance(content, list):
            conv = []
            for item in content:
                if isinstance(item, dict):
                    t = item.get("type", "")
                    if t in ("text", "input_text"):
                        # Only use input_text for user messages
                        if role == "user":
                            conv.append({"type": "input_text", "text": item.get("text", "")})
                        else:
                            # Assistant messages - use plain text
                            conv.append(item.get("text", ""))
                    elif t == "image_url":
                        img = item.get("image_url", {})
                        img_url = img.get("url") if isinstance(img, dict) else str(img)
                        if img_url:
                            conv.append({"type": "input_image", "image_url": img_url})
                    elif t == "input_image":
                        img = item.get("image_url", {})
                        img_url = img.get("url") if isinstance(img, dict) else str(img) if img else ""
                        if img_url:
                            conv.append({"type": "input_image", "image_url": img_url})
                        else:
                            conv.append(item)
                    else:
                        conv.append(item)
                else:
                    # Plain text item - use input_text only for user
                    if role == "user":
                        conv.append({"type": "input_text", "text": str(item)})
                    else:
                        conv.append(str(item))
            if conv:
                items.append({"role": role, "content": conv})
        elif content:
            # Plain text content - no wrapper for assistant
            if role == "user":
                items.append({"role": role, "content": [{"type": "input_text", "text": str(content)}]})
            else:
                items.append({"role": role, "content": str(content)})
    return items

def _convert_tools_schema(tools: List[Dict]) -> List[Dict]:
    """Convert Chat-style tools to Responses API format."""
    import copy
    converted = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        tool_type = tool.get("type", "")
        if tool_type == "function":
            func = tool.get("function", {})
            # Deep copy parameters to avoid mutating the original
            params = copy.deepcopy(func.get("parameters", {}))
            
            # Ensure additionalProperties: false
            if "additionalProperties" not in params:
                params["additionalProperties"] = False
            
            # With strict: true, required must include ALL properties
            if "properties" in params and isinstance(params["properties"], dict):
                required = params.get("required", [])
                if isinstance(required, list):
                    # Add any missing properties to required
                    for prop in params["properties"]:
                        if prop not in required:
                            required.append(prop)
                    params["required"] = required
            
            converted.append({
                "type": "function",
                "name": func.get("name", ""),
                "description": func.get("description", ""),
                "parameters": params,
                "strict": True,
            })
        else:
            converted.append(tool)
    return converted

class BaseProvider:
    """Base class for LLM providers."""

    def __init__(self, name: str, api_base: str, api_key_env: str = ''):
        self.name = name
        self.api_base = api_base
        self.api_key_env = api_key_env
        self.timeout = 120.0

    def _get_headers(self) -> Dict[str, str]:
        """Get request headers."""
        api_key = os.environ.get(self.api_key_env) if self.api_key_env else ''
        if not api_key:
            api_key = config.llm.get('api_key', '')
        return {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
    
    def _check_api_key(self) -> Optional[Dict]:
        """Check if API key is configured, return error dict if not.
        
        Note: If api_key_env is empty (e.g., for local providers like Ollama), skip the check.
        """
        # Skip check if api_key_env is empty (local providers like Ollama)
        if not self.api_key_env:
            return None
        
        api_key = os.environ.get(self.api_key_env) if self.api_key_env else ''
        if not api_key:
            api_key = config.llm.get('api_key', '')
        
        if not api_key:
            return {
                "error": {
                    "message": "LLM API key not configured. Please configure api_key in webchat settings.",
                    "type": "configuration_error",
                    "code": "api_key_missing"
                }
            }
        return None

    async def chat(self, **kwargs) -> Dict[str, Any]:
        raise NotImplementedError

    def list_models(self) -> List[str]:
        return []

    async def _call_api(self, endpoint: str, payload: Dict) -> Dict:
        """Make API call with retry logic and debug logging."""
        # Check if API key is configured
        error = self._check_api_key()
        if error:
            return error
        
        headers = self._get_headers()
        url = f"{self.api_base}{endpoint}"

        # Debug: Log request
        if _is_debug_enabled():
            _log_request("LLM", url, "POST", headers, payload)

        last_error = None
        max_retries = config.llm.get('max_retries', 3)
        retry_delay = config.llm.get('retry_delay', 1)

        for attempt in range(max_retries):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await client.post(
                        url,
                        headers=headers,
                        json=payload
                    )

                    # Debug: Log response
                    if _is_debug_enabled():
                        _log_response("LLM", response.status_code, response.headers)

                    response.raise_for_status()
                    result = response.json()

                    # Debug: Log response body (truncated)
                    if _is_debug_enabled():
                        result_str = json.dumps(result, indent=2, default=str)
                        logger.debug(f"Body: {_truncate_text(result_str, 1000)}")

                    return result

            except (httpx.HTTPStatusError, httpx.RequestError) as e:
                last_error = e
                if _is_debug_enabled():
                    logger.debug(f"=== [LLM] ERROR ===")
                    logger.debug(f"Attempt: {attempt + 1}/{max_retries}")
                    logger.debug(f"Error: {type(e).__name__}: {e}")

                if attempt < max_retries - 1:
                    delay = retry_delay * (2 ** attempt)
                    logger.warning(f"API error, retrying in {delay}s: {e}")
                    # Log error response body for debugging
                    if hasattr(e, 'response') and e.response is not None:
                        try:
                            error_body = e.response.text
                            logger.warning(f"Error response: {error_body[:500]}")
                        except:
                            pass
                    await asyncio.sleep(delay)

        raise last_error


class OpenAIProvider(BaseProvider):
    """OpenAI GPT provider."""
    
    def __init__(self):
        super().__init__(
            name="openai",
            api_base=config.llm.get('api_base', 'https://api.openai.com/v1'),
            api_key_env='EFP_LLM_API_KEY'
        )
        self.default_model = config.llm.get('model', 'gpt-3.5-turbo')
    
    async def chat(
        self,
        messages: List[Dict],
        system_prompt: Optional[str] = None,
        tools: Optional[List[Dict]] = None,
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        reasoning_replay: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Call OpenAI Chat Completions API with reasoning_replay support.
        
        Args:
            reasoning_replay: Enable reasoning_replay to see model's internal reasoning.
                When enabled, includes model's thinking process in response.
        """
        all_messages = []
        if system_prompt:
            all_messages.append({"role": "system", "content": system_prompt})
        all_messages.extend(messages)
        
        # Use config setting if not explicitly provided
        enable_reasoning = reasoning_replay if reasoning_replay is not None else config.llm.get('reasoning_replay', False)
        
        # GPT-5 models require max_completion_tokens instead of max_tokens
        model_name = (model or self.default_model).lower()
        if model_name.startswith("gpt-5"):
            max_tokens_key = "max_completion_tokens"
        else:
            max_tokens_key = "max_tokens"
        
        # GPT-5 models don't support temperature parameter
        include_temperature = not model_name.startswith("gpt-5")
        
        payload = {
            "model": model or self.default_model,
            "messages": all_messages,
        }
        if include_temperature:
            payload["temperature"] = temperature
        payload[max_tokens_key] = max_tokens or config.llm.get('max_tokens', 1000)
        
        # Add reasoning_replay if enabled (for o1/o3 style reasoning)
        # Only supported by specific models: o1, o3, o1-mini, o1-pro, etc.
        if enable_reasoning:
            model_name = (model or self.default_model).lower()
            # Check if model supports reasoning_effort/reasoning
            if any(m in model_name for m in ['o1', 'o3', 'o2']):
                payload["reasoning"] = {"type": "text"}
                if _is_debug_enabled():
                    logger.debug(f"Reasoning replay: enabled for model {model_name}")
            else:
                logger.warning(f"Model {model_name} does not support reasoning_replay parameter, ignoring")
        
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        
        # Debug: Log chat request details
        if _is_debug_enabled():
            logger.debug(f"=== [LLM] CHAT REQUEST ===")
            logger.debug(f"Provider: {self.name}")
            logger.debug(f"Model: {payload['model']}")
            logger.debug(f"Messages count: {len(all_messages)}")
            if system_prompt:
                logger.debug(f"System prompt: {truncate(system_prompt, 200)}")
            logger.debug(f"Messages preview:")
            for i, msg in enumerate(all_messages[:5]):
                role = msg.get("role", "unknown")
                content = truncate(msg.get("content") or "", 100)
                logger.debug(f"  [{i}] {role}: {content}")
            if len(all_messages) > 5:
                logger.debug(f"  ... [{len(all_messages) - 5} more messages]")
            
            if tools:
                logger.debug(f"Tools count: {len(tools)}")
                for i, tool in enumerate(tools):
                    tool_name = tool.get("function", {}).get("name", f"tool_{i}")
                    logger.debug(f"  Tool {i}: {tool_name}")
        
        data = await self._call_api("/chat/completions", payload)
        
        choice = data["choices"][0]
        message = choice["message"]
        
        # Debug: Log response details
        if _is_debug_enabled():
            logger.debug(f"=== [LLM] CHAT RESPONSE ===")
            logger.debug(f"Finish reason: {choice.get('finish_reason', 'unknown')}")
            content = message.get("content") or ""
            logger.debug(f"Content length: {len(content)} chars")
            if content:
                logger.debug(f"Content preview: {truncate(content, 200)}")
            else:
                logger.debug("Content: (empty - tool call response)")
            
            # Log reasoning if present
            reasoning = message.get("reasoning")
            if reasoning:
                logger.debug(f"Reasoning length: {len(reasoning)} chars")
                logger.debug(f"Reasoning preview: {truncate(reasoning, 200)}")
            
            tool_calls = message.get("tool_calls", [])
            logger.debug(f"Tool calls: {len(tool_calls)}")
            for tc in tool_calls:
                tc_id = tc.get("id", "unknown")
                tc_name = tc.get("function", {}).get("name", "unknown")
                logger.debug(f"  - {tc_name} (id={tc_id})")
        
        # Build result with reasoning if available
        result = {
            "content": message.get("content", ""),
            "tool_calls": message.get("tool_calls", []),
            "usage": data.get("usage", {}),
        }
        
        # Include reasoning if present
        if message.get("reasoning"):
            result["reasoning"] = message.get("reasoning")
        
        # Calculate cost using centralized pricing
        usage = result["usage"]
        input_tokens = usage.get("prompt_tokens", 0)
        output_tokens = usage.get("completion_tokens", 0)
        model_name = data.get("model", self.default_model)
        
        cost = estimate_cost(model_name, input_tokens, output_tokens)
        usage["cost_usd"] = cost
        
        # Record usage for tracking
        usage_tracker.record_usage(
            provider=self.name,
            model=model_name,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            session_id="llm_api",
            task_type="chat",
        )
        
        return result

    async def responses(
        self,
        messages: Optional[List[Dict]] = None,
        input_items: Optional[List[Dict]] = None,
        system_prompt: Optional[str] = None,
        tools: Optional[List[Dict]] = None,
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
        reasoning_replay: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Call OpenAI Responses API (/responses endpoint)."""
        # Fall back to chat() when reasoning_replay is needed (not supported)
        if reasoning_replay:
            # Convert input_items to messages for chat()
            chat_messages = []
            if input_items:
                for item in input_items:
                    if item.get("type") == "message":
                        chat_messages.append({"role": item.get("role", "user"), "content": item.get("content", "")})
            return await self.chat(messages=chat_messages, system_prompt=system_prompt, tools=tools, model=model, max_tokens=max_tokens, reasoning_replay=reasoning_replay)
        
        model_name = model or self.default_model
        
        # Convert messages to input_items if provided
        if input_items is None and messages is not None:
            input_items = _convert_messages_to_input_items(messages)
        elif input_items is None:
            input_items = []
        
        # Convert tools from Chat format to Responses format
        converted_tools = None
        if tools:
            converted_tools = _convert_tools_schema(tools)
        # Note: messages are already converted to input_items above
        
        # Build payload for Responses API
        payload = {
            "model": model_name,
            "instructions": system_prompt or "",
            "input": input_items,
            "max_output_tokens": max_tokens or config.llm.get('max_tokens', 1000),
            "text": {"format": {"type": "text"}},
        }
        
        # Add tools if provided (Responses API format)
        if converted_tools:
            payload["tools"] = converted_tools
            payload["tool_choice"] = "auto"
        
        # Debug: Log request details (before calling _call_api)
        if _is_debug_enabled():
            logger.debug(f"=== [LLM] RESPONSES API REQUEST ===")
            logger.debug(f"Provider: {self.name}")
            logger.debug(f"Model: {model_name}")
            logger.debug(f"Instructions: {truncate(system_prompt or '', 200)}")
            logger.debug(f"Input items count: {len(input_items)}")
        
        # Use _call_api for centralized retry/backoff behavior
        data = await self._call_api("/responses", payload)
        
        # _call_api may return {"error": ...} (e.g., when API key is missing); propagate that directly
        if isinstance(data, dict) and data.get("error"):
            return data
        
        # Debug: Log response
        if _is_debug_enabled():
            logger.debug(f"=== [LLM] RESPONSES API RESPONSE ===")
        
        # Parse response - Responses API uses 'output' array instead of 'choices'
        output_items = data.get("output", [])
        content = ""
        function_calls = []
        
        for item in output_items:
            item_type = item.get("type", "")
            
            if item_type == "message":
                msg_content = item.get("content", [])
                if isinstance(msg_content, list):
                    for msg_item in msg_content:
                        if msg_item.get("type") == "output_text":
                            content += msg_item.get("text", "")
                        elif msg_item.get("type") == "function_call":
                            function_calls.append({
                                "call_id": msg_item.get("call_id", ""),
                                "name": msg_item.get("name", ""),
                                "arguments": msg_item.get("arguments", {}),
                            })
                elif isinstance(msg_content, str):
                    content += msg_content
            
            elif item_type == "function_call":
                function_calls.append({
                    "call_id": item.get("call_id", ""),
                    "name": item.get("name", ""),
                    "arguments": item.get("arguments", {}),
                })
        
        # Calculate usage
        usage_data = data.get("usage", {})
        prompt_tokens = usage_data.get("input_tokens", 0)
        completion_tokens = usage_data.get("output_tokens", 0)
        
        # Estimate if not provided
        if prompt_tokens == 0:
            prompt_tokens = sum(len(str(m).split()) * 4 for m in input_items)
        if completion_tokens == 0:
            completion_tokens = len(content.split()) * 4
        
        # Build function_calls (Responses API format)
        function_calls_result = []
        for fc in function_calls:
            args = fc.get("arguments", {})
            arguments = json.dumps(args) if isinstance(args, dict) else args
            function_calls_result.append({
                "call_id": fc.get("call_id", ""),
                "name": fc.get("name", ""),
                "arguments": arguments,
            })
        
        # Also include tool_calls for backward compatibility
        tool_calls_compat = []
        for fc in function_calls:
            args = fc.get("arguments", {})
            arguments = json.dumps(args) if isinstance(args, dict) else args
            tool_calls_compat.append({
                "id": fc.get("call_id", ""),
                "type": "function",
                "function": {
                    "name": fc.get("name", ""),
                    "arguments": arguments
                }
            })
        
        result = {
            "content": content,
            "function_calls": function_calls_result,
            "tool_calls": tool_calls_compat,
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            }
        }
        
        # Calculate cost and record usage
        cost = estimate_cost(model_name, prompt_tokens, completion_tokens)
        result["usage"]["cost_usd"] = cost
        
        # Record usage for tracking
        usage_tracker.record_usage(
            provider=self.name,
            model=model_name,
            input_tokens=prompt_tokens,
            output_tokens=completion_tokens,
            session_id="llm_api",
            task_type="responses",
        )
        
        return result

    def list_models(self) -> List[str]:
        return [
            "gpt-3.5-turbo",
            "gpt-3.5-turbo-16k",
            "gpt-4",
            "gpt-4-turbo",
            "gpt-4o",
            "gpt-4o-mini",
        ]


class GitHubCopilotProvider(BaseProvider):
    """GitHub Copilot provider."""
    
    def __init__(self):
        super().__init__(
            name="github_copilot",
            api_base=config.llm.get('api_base', 'https://api.githubcopilot.com'),
            api_key_env='GITHUB_COPILOT_TOKEN'
        )
        self.default_model = config.llm.get('model', 'gpt-5-mini')
    
    async def chat(
        self,
        messages: List[Dict],
        system_prompt: Optional[str] = None,
        tools: Optional[List[Dict]] = None,
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        reasoning_replay: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Call GitHub Copilot Chat API."""
        # Check if API key is configured
        api_key = os.environ.get('GITHUB_COPILOT_TOKEN') or config.llm.get('api_key', '')
        if not api_key:
            return {
                "error": {
                    "message": "LLM API key not configured. Please configure api_key in webchat settings.",
                    "type": "configuration_error",
                    "code": "api_key_missing"
                }
            }
        
        import os
        headers = {
            "Authorization": f"Bearer {os.environ.get('GITHUB_COPILOT_TOKEN', config.llm.get('api_key', ''))}",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2023-06-01",
            "Accept": "application/vnd.github.copilot-chat-preview+json",
        }
        
        # Build messages - GitHub Copilot uses system differently
        all_messages = []
        if system_prompt:
            # GitHub Copilot: prepend system message to conversation
            all_messages.append({"role": "system", "content": system_prompt})
        all_messages.extend(messages)
        
        payload = {
            "model": model or self.default_model,
            "messages": all_messages,
        }
        
        # Add tools support (similar to OpenAI)
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        
        # Debug: Log request
        if _is_debug_enabled():
            logger.debug(f"=== [{self.name.upper()}] REQUEST ===")
            logger.debug(f"Model: {payload['model']}")
            logger.debug(f"Messages count: {len(all_messages)}")
            if system_prompt:
                logger.debug(f"System prompt: {truncate(system_prompt, 200)}")
            logger.debug(f"Messages preview:")
            for i, msg in enumerate(all_messages[:5]):
                role = msg.get("role", "unknown")
                content = truncate(msg.get("content") or "", 100)
                logger.debug(f"  [{i}] {role}: {content}")
            if len(all_messages) > 5:
                logger.debug(f"  ... [{len(all_messages) - 5} more messages]")
            
            if tools:
                logger.debug(f"Tools count: {len(tools)}")
                for i, tool in enumerate(tools):
                    tool_name = tool.get("function", {}).get("name", f"tool_{i}")
                    logger.debug(f"  Tool {i}: {tool_name}")

        
        # Make API call with proper error handling
        endpoint = "/chat/completions"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.api_base}{endpoint}",
                    headers=headers,
                    json=payload
                )
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPStatusError as e:
            error = handle_httpx_error(e, provider=self.name, endpoint=endpoint)
            log_error(error, component=self.name.upper())
            raise error
        except httpx.RequestError as e:
            error = handle_httpx_request_error(e, provider=self.name, endpoint=endpoint)
            log_error(error, component=self.name.upper())
            raise error
        
        # Debug: Log response
        if _is_debug_enabled():
            logger.debug(f"=== [{self.name.upper()}] RESPONSE ===")
            logger.debug(f"Status: {response.status_code}")
        
        choice = data["choices"][0]
        message = choice["message"]
        
        # Debug: Log response details
        if _is_debug_enabled():
            logger.debug(f"=== [{self.name.upper()}] CHAT RESPONSE ===")
            logger.debug(f"Finish reason: {choice.get('finish_reason', 'unknown')}")
            content = message.get("content") or ""
            logger.debug(f"Content length: {len(content)} chars")
            if content:
                logger.debug(f"Content preview: {truncate(content, 200)}")
            else:
                logger.debug("Content: (empty - tool call response)")
            # Log reasoning if present
            reasoning = message.get("reasoning")
            if reasoning:
                logger.debug(f"Reasoning length: {len(reasoning)} chars")
                logger.debug(f"Reasoning preview: {truncate(reasoning, 200)}")
            tool_calls = message.get("tool_calls", [])
            logger.debug(f"Tool calls: {len(tool_calls)}")
            for tc in tool_calls:
                tc_id = tc.get("id", "unknown")
                tc_name = tc.get("function", {}).get("name", "unknown")
                logger.debug(f"  - {tc_name} (id={tc_id})")
        
        # Build result with reasoning if available
        result = {
            "content": message.get("content", ""),
            "tool_calls": message.get("tool_calls", []),
            "usage": data.get("usage", {}),
        }
        
        # Include reasoning if present
        if message.get("reasoning"):
            result["reasoning"] = message.get("reasoning")
        
        # Calculate cost using centralized pricing
        usage = result["usage"]
        input_tokens = usage.get("prompt_tokens", 0)
        output_tokens = usage.get("completion_tokens", 0)
        model_name = data.get("model", self.default_model)
        cost = estimate_cost(self.default_model, input_tokens, output_tokens)
        usage["cost_usd"] = cost
        
        # Record usage for tracking
        usage_tracker.record_usage(
            provider=self.name,
            model=self.default_model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            session_id="llm_api",
            task_type="chat",
        )
        
        return result

    async def responses(
        self,
        messages: Optional[List[Dict]] = None,
        input_items: Optional[List[Dict]] = None,
        system_prompt: Optional[str] = None,
        tools: Optional[List[Dict]] = None,
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
        reasoning_replay: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Call GitHub Copilot Responses API (/responses endpoint).
        
        Note: The Responses API has different payload format than Chat Completions:
        - System prompt goes to 'instructions' field
        - Messages go to 'input' array
        - max_tokens -> max_output_tokens
        - tools not fully supported in Responses API
        - reasoning_replay not supported in Responses API
        
        When reasoning is needed, fall back to chat() for reliable support.
        """
        # Fall back to chat() when reasoning_replay is needed (not supported)

        if reasoning_replay:
            logger.info(f"[GitHubCopilot] reasoning_replay enabled, falling back to chat()")
            return await self.chat(
                messages=messages,
                system_prompt=system_prompt,
                tools=tools,
                model=model,
                max_tokens=max_tokens,
                reasoning_replay=reasoning_replay,
            )
        
        # Check if API key is configured
        api_key = os.environ.get('GITHUB_COPILOT_TOKEN') or config.llm.get('api_key', '')
        if not api_key:
            return {
                "error": {
                    "message": "LLM API key not configured. Please configure api_key in webchat settings.",
                    "type": "configuration_error",
                    "code": "api_key_missing"
                }
            }
        
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2023-06-01",
        }
        
        model_name = model or self.default_model
        
        # Convert messages to input_items if provided
        if input_items is None and messages is not None:
            input_items = _convert_messages_to_input_items(messages)
        elif input_items is None:
            input_items = []
        
        # Convert tools from Chat format to Responses format
        converted_tools = None
        if tools:
            converted_tools = _convert_tools_schema(tools)
        # Note: messages are already converted to input_items above
        
        # Build payload for Responses API
        payload = {
            "model": model_name,
            "instructions": system_prompt or "",
            "input": input_items,
            "max_output_tokens": max_tokens or config.llm.get('max_tokens', 1000),
            "text": {"format": {"type": "text"}},
        }
        
        # Add tools if provided (Responses API format)
        if converted_tools:
            payload["tools"] = converted_tools
            payload["tool_choice"] = "auto"
        
        # Debug: Log request details (before calling _call_api)
        if _is_debug_enabled():
            logger.debug(f"=== [GITHUB COPILOT] RESPONSES API REQUEST ===")
            logger.debug(f"Model: {model_name}")
            logger.debug(f"Instructions: {truncate(system_prompt or '', 200)}")
            logger.debug(f"Input messages count: {len(input_items)}")
        
        # Make API call
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.api_base}/responses",
                    headers=headers,
                    json=payload
                )
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPStatusError as e:
            error = handle_httpx_error(e, provider=self.name, endpoint="/responses")
            log_error(error, component=self.name.upper())
            raise error
        except httpx.RequestError as e:
            error = handle_httpx_request_error(e, provider=self.name, endpoint="/responses")
            log_error(error, component=self.name.upper())
            raise error
        
        # Debug: Log response
        if _is_debug_enabled():
            logger.debug(f"=== [GITHUB COPILOT] RESPONSES API RESPONSE ===")
            logger.debug(f"Status: {response.status_code}")
        
        # Parse response - Responses API uses 'output' array instead of 'choices'
        output_items = data.get("output", [])
        content = ""
        function_calls = []
        
        for item in output_items:
            item_type = item.get("type", "")
            
            if item_type == "message":
                msg_content = item.get("content", [])
                if isinstance(msg_content, list):
                    for msg_item in msg_content:
                        if msg_item.get("type") == "output_text":
                            content += msg_item.get("text", "")
                        elif msg_item.get("type") == "function_call":
                            function_calls.append({
                                "call_id": msg_item.get("call_id", ""),
                                "name": msg_item.get("name", ""),
                                "arguments": msg_item.get("arguments", {}),
                            })
                elif isinstance(msg_content, str):
                    content += msg_content
            
            elif item_type == "function_call":
                function_calls.append({
                    "call_id": item.get("call_id", ""),
                    "name": item.get("name", ""),
                    "arguments": item.get("arguments", {}),
                })
        
        # Calculate usage
        usage_data = data.get("usage", {})
        prompt_tokens = usage_data.get("input_tokens", 0)
        completion_tokens = usage_data.get("output_tokens", 0)
        
        # Estimate if not provided
        if prompt_tokens == 0:
            prompt_tokens = sum(len(str(m).split()) * 4 for m in input_items)
        if completion_tokens == 0:
            completion_tokens = len(content.split()) * 4
        
        # Build function_calls (Responses API format)
        function_calls_result = []
        for fc in function_calls:
            args = fc.get("arguments", {})
            arguments = json.dumps(args) if isinstance(args, dict) else args
            function_calls_result.append({
                "call_id": fc.get("call_id", ""),
                "name": fc.get("name", ""),
                "arguments": arguments,
            })
        
        # Also include tool_calls for backward compatibility
        tool_calls_compat = []
        for fc in function_calls:
            args = fc.get("arguments", {})
            arguments = json.dumps(args) if isinstance(args, dict) else args
            tool_calls_compat.append({
                "id": fc.get("call_id", ""),
                "type": "function",
                "function": {
                    "name": fc.get("name", ""),
                    "arguments": arguments
                }
            })
        
        result = {
            "content": content,
            "function_calls": function_calls_result,
            "tool_calls": tool_calls_compat,
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            }
        }
        
        # Calculate cost and record usage
        cost = estimate_cost(model_name, prompt_tokens, completion_tokens)
        result["usage"]["cost_usd"] = cost
        
        # Record usage for tracking
        usage_tracker.record_usage(
            provider=self.name,
            model=model_name,
            input_tokens=prompt_tokens,
            output_tokens=completion_tokens,
            session_id="llm_api",
            task_type="responses",
        )
        
        return result
    
    def list_models(self) -> List[str]:
        return ["gpt-4", "gpt-4-turbo"]


class ClaudeProvider(BaseProvider):
    """Anthropic Claude provider."""
    
    def __init__(self):
        super().__init__(
            name="claude",
            api_base=config.llm.get('api_base', 'https://api.anthropic.com'),
            api_key_env='ANTHROPIC_API_KEY'
        )
        self.default_model = "claude-sonnet-4-20250514"
    
    def _get_headers(self) -> Dict[str, str]:
        import os
        api_key = os.environ.get('ANTHROPIC_API_KEY') or config.llm.get('api_key', '')
        return {
            "x-api-key": api_key,
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
        }
    
    async def chat(
        self,
        messages: List[Dict],
        system_prompt: Optional[str] = None,
        tools: Optional[List[Dict]] = None,
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        reasoning_replay: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Call Anthropic Claude API."""
        # Check if API key is configured
        api_key = os.environ.get('ANTHROPIC_API_KEY') or config.llm.get('api_key', '')
        if not api_key:
            return {
                "error": {
                    "message": "LLM API key not configured. Please configure api_key in webchat settings.",
                    "type": "configuration_error",
                    "code": "api_key_missing"
                }
            }
        
        all_messages = []
        for msg in messages:
            if msg["role"] == "system" and system_prompt:
                continue
            all_messages.append({"role": msg["role"], "content": msg["content"]})
        
        payload = {
            "model": model or self.default_model,
            "messages": all_messages,
            "max_tokens": max_tokens or 4096,
            "temperature": temperature,
        }
        
        if system_prompt:
            payload["system"] = system_prompt
        
        if tools:
            payload["tools"] = self._convert_tools_to_claude(tools)
        
        # Debug: Log request
        if _is_debug_enabled():
            logger.debug(f"=== [{self.name.upper()}] REQUEST ===")
            logger.debug(f"Model: {payload['model']}")
            logger.debug(f"Messages count: {len(all_messages)}")
        
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.api_base}/messages",
                headers=self._get_headers(),
                json=payload
            )
            response.raise_for_status()
            data = response.json()
        
        # Debug: Log response
        if _is_debug_enabled():
            logger.debug(f"=== [{self.name.upper()}] RESPONSE ===")
            logger.debug(f"Status: {response.status_code}")
        
        # Parse and log content/tool calls
        parsed = self._parse_response(data)
        
        # Debug: Log content and tool calls
        if _is_debug_enabled():
            content = parsed.get("content", "")
            tool_calls = parsed.get("tool_calls", [])
            logger.debug(f"Content length: {len(content)} chars")
            logger.debug(f"Content preview: {_truncate_text(content, 200)}")
            logger.debug(f"Tool calls: {len(tool_calls)}")
            for tc in tool_calls:
                tc_name = tc.get("function", {}).get("name", "unknown")
                logger.debug(f"  - {tc_name}")
        
        return parsed
    
    def _convert_tools_to_claude(self, tools: List[Dict]) -> List[Dict]:
        """Convert OpenAI-style tools to Claude format."""
        claude_tools = []
        for tool in tools:
            func = tool.get("function", {})
            claude_tools.append({
                "name": func.get("name", ""),
                "description": func.get("description", ""),
                "input_schema": func.get("parameters", {})
            })
        return claude_tools
    
    def _parse_response(self, data: Dict) -> Dict[str, Any]:
        """Parse Claude response with reasoning support."""
        content_blocks = data.get("content", [])
        text_content = ""
        reasoning_content = ""
        tool_calls = []
        
        for block in content_blocks:
            if block.get("type") == "text":
                text_content = block.get("text", "")
            elif block.get("type") == "reasoning":
                # Claude's reasoning content
                reasoning_content = block.get("text", "")
            elif block.get("type") == "tool_use":
                tool_calls.append({
                    "id": block.get("id", ""),
                    "type": "function",
                    "function": {
                        "name": block.get("name", ""),
                        "arguments": json.dumps(block.get("input", {}))
                    }
                })
        
        usage = data.get("usage", {})
        result = {
            "content": text_content,
            "tool_calls": tool_calls,
            "usage": {
                "prompt_tokens": usage.get("input_tokens", 0),
                "completion_tokens": usage.get("output_tokens", 0),
                "total_tokens": usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
            }
        }
        
        # Include reasoning if present
        if reasoning_content:
            result["reasoning"] = reasoning_content
        
        # Calculate cost and record usage
        input_tokens = usage.get("input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0)
        cost = estimate_cost(self.default_model, input_tokens, output_tokens)
        result["usage"]["cost_usd"] = cost
        
        usage_tracker.record_usage(
            provider=self.name,
            model=self.default_model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            session_id="llm_api",
            task_type="chat",
        )
        
        return result
    
    def list_models(self) -> List[str]:
        return [
            "claude-sonnet-4-20250514",
            "claude-opus-4-20250514",
            "claude-haiku-4-20250514",
            "claude-3-5-sonnet",
            "claude-3-opus",
            "claude-3-haiku",
        ]


class OllamaProvider(BaseProvider):
    """Ollama local LLM provider."""
    
    def __init__(self):
        super().__init__(
            name="ollama",
            api_base=config.llm.get('api_base', 'http://127.0.0.1:11434'),
            api_key_env=''
        )
        self.default_model = "llama3"
    
    async def chat(
        self,
        messages: List[Dict],
        system_prompt: Optional[str] = None,
        tools: Optional[List[Dict]] = None,
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        reasoning_replay: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Call Ollama API."""
        full_messages = []
        if system_prompt:
            full_messages.append({"role": "system", "content": system_prompt})
        full_messages.extend(messages)
        
        payload = {
            "model": model or self.default_model,
            "messages": full_messages,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens or config.llm.get('max_tokens', 1000),
            }
        }
        
        # Add tools support for Ollama (format varies by model version)
        if tools:
            # Convert OpenAI-style tools to Ollama format if needed
            # Note: Not all Ollama models support tools - check your model
            ollama_tools = []
            for tool in tools:
                func = tool.get("function", {})
                ollama_tools.append({
                    "type": "function",
                    "function": {
                        "name": func.get("name", ""),
                        "description": func.get("description", ""),
                        "parameters": func.get("parameters", {}),
                    }
                })
            payload["tools"] = ollama_tools
        
        # Debug: Log request
        if _is_debug_enabled():
            logger.debug(f"=== [{self.name.upper()}] REQUEST ===")
            logger.debug(f"Model: {payload['model']}")
            logger.debug(f"Messages count: {len(full_messages)}")
        
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(
                    f"{self.api_base}/api/chat",
                    json=payload
                )
                response.raise_for_status()
                data = response.json()
        except httpx.ConnectError:
            if _is_debug_enabled():
                logger.debug(f"=== [{self.name.upper()}] ERROR ===")
                logger.debug("Ollama not running. Start with: ollama serve")
            return {
                "content": "",
                "tool_calls": [],
                "error": "Ollama not running. Start with: ollama serve",
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
            }
        
        # Debug: Log response
        if _is_debug_enabled():
            logger.debug(f"=== [{self.name.upper()}] RESPONSE ===")
            logger.debug(f"Status: {response.status_code}")
        
        # Parse and log result
        result = self._parse_response(data)
        
        # Debug: Log content and tool calls
        if _is_debug_enabled():
            content = result.get("content", "")
            tool_calls = result.get("tool_calls", [])
            logger.debug(f"Content length: {len(content)} chars")
            logger.debug(f"Content preview: {_truncate_text(content, 200)}")
            logger.debug(f"Tool calls: {len(tool_calls)}")
            for tc in tool_calls:
                tc_name = tc.get("function", {}).get("name", "unknown")
                logger.debug(f"  - {tc_name}")
        
        # Calculate cost (Ollama is free, but we track tokens)
        usage = result.get("usage", {})
        input_tokens = usage.get("prompt_eval_count", usage.get("prompt_tokens", 0))
        output_tokens = usage.get("eval_count", usage.get("output_tokens", 0))
        cost = estimate_cost(self.default_model, input_tokens, output_tokens)
        result["usage"]["cost_usd"] = cost
        
        usage_tracker.record_usage(
            provider=self.name,
            model=self.default_model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            session_id="llm_api",
            task_type="chat",
        )
        
        return result
    
    def _parse_response(self, data: Dict) -> Dict[str, Any]:
        """Parse Ollama response with reasoning support."""
        message = data.get("message", {})
        content = message.get("content", "")
        reasoning = message.get("reasoning", "") or data.get("reasoning", "")
        
        # Parse tool_calls from Ollama response
        tool_calls = []
        raw_tool_calls = message.get("tool_calls", [])
        if not raw_tool_calls:
            # Try alternative location
            raw_tool_calls = data.get("message", {}).get("tool_calls", [])
        
        for tc in raw_tool_calls:
            # Ollama format: {"function": {"name": "...", "arguments": "..."}}
            func_data = tc.get("function", tc)
            tool_calls.append({
                "id": tc.get("id", f"call_{len(tool_calls)}"),
                "type": "function",
                "function": {
                    "name": func_data.get("name", ""),
                    "arguments": func_data.get("arguments", "{}")
                }
            })
        
        result = {
            "content": content,
            "tool_calls": tool_calls,
            "usage": {
                "prompt_tokens": data.get("prompt_eval_count", 0),
                "completion_tokens": data.get("eval_count", 0),
                "total_tokens": data.get("prompt_eval_count", 0) + data.get("eval_count", 0),
            }
        }
        
        # Include reasoning if present
        if reasoning:
            result["reasoning"] = reasoning
        
        return result
    
    def list_models(self) -> List[str]:
        """List available Ollama models."""
        return [self.default_model]
    
    async def list_available_models(self) -> List[str]:
        """List available Ollama models from server."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(f"{self.api_base}/api/tags")
                response.raise_for_status()
                data = response.json()
                return [m["name"] for m in data.get("models", [])]
        except:
            return [self.default_model]
    
    async def pull_model(self, model: str) -> Dict[str, Any]:
        """Pull a model from Ollama registry."""
        async with httpx.AsyncClient(timeout=300.0) as client:
            response = await client.post(
                f"{self.api_base}/api/pull",
                json={"name": model, "stream": False}
            )
            response.raise_for_status()
            return response.json()


class LLMClient:
    """Unified LLM client supporting multiple providers."""
    
    # Backward compatibility constants
    COPILOT_API_BASE = "https://api.github.com/copilot"
    
    def __init__(self):
        self.providers: Dict[str, BaseProvider] = {}
        self.default_provider = None
        self._init_providers()
    
    def _init_providers(self):
        """Initialize available LLM providers."""
        provider = config.llm.get('provider', 'openai')
        
        if provider in ('openai', None):
            self.providers['openai'] = OpenAIProvider()
            if not self.default_provider:
                self.default_provider = 'openai'
        
        if provider == 'github_copilot':
            self.providers['github_copilot'] = GitHubCopilotProvider()
            self.default_provider = 'github_copilot'
        
        # Always register Claude and Ollama as options
        self.providers['claude'] = ClaudeProvider()
        self.providers['ollama'] = OllamaProvider()
        
        logger.info(f"LLMClient initialized with providers: {list(self.providers.keys())}")
    
    def reinit(self):
        """Reinitialize LLM providers (called when config changes)."""
        logger.info("Reinitializing LLM providers...")
        self.providers = {}
        self.default_provider = None
        self._init_providers()
        logger.info("LLM providers reinitialized")
    
    # Backward compatibility methods
    def _get_headers(self) -> Dict[str, str]:
        """Get headers for API requests (backward compatibility)."""
        return self.providers.get('github_copilot', GitHubCopilotProvider())._get_headers()
    
    def _get_chat_endpoint(self) -> str:
        """Get the chat API endpoint (backward compatibility)."""
        return self.COPILOT_API_BASE + "/chat"
    
    def is_github_copilot(self) -> bool:
        """Check if GitHub Copilot is configured as provider."""
        return 'github_copilot' in self.providers
    
    async def chat(
        self,
        messages: List[Dict[str, str]],
        system_prompt: Optional[str] = None,
        tools: Optional[List[Dict]] = None,
        model: Optional[str] = None,
        provider: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        reasoning_replay: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Chat with LLM.
        
        Args:
            reasoning_replay: Enable reasoning_replay to see model's internal reasoning.
        """
        provider = provider or self.default_provider or 'openai'
        
        if provider not in self.providers:
            logger.error(f"Unknown provider: {provider}, using openai")
            provider = 'openai'
        
        client = self.providers[provider]
        
        return await client.chat(
            messages=messages,
            system_prompt=system_prompt,
            tools=tools,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            reasoning_replay=reasoning_replay,
        )
    
    async def responses(
        self,
        messages: Optional[List[Dict[str, Any]]] = None,
        input_items: Optional[List[Dict]] = None,
        system_prompt: Optional[str] = None,
        tools: Optional[List[Dict]] = None,
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
        provider: Optional[str] = None,
        reasoning_replay: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Call LLM using Responses API (/responses endpoint)."""
        provider = provider or self.default_provider or 'openai'
        
        if provider not in self.providers:
            logger.error(f"Unknown provider: {provider}, using openai")
            provider = 'openai'
        
        client = self.providers[provider]
        
        # Check if provider supports responses() method
        if not hasattr(client, 'responses'):
            logger.warning(f"Provider {provider} does not support responses() method, falling back to chat()")
            return await self.chat(
                messages=messages,
                system_prompt=system_prompt,
                tools=tools,
                model=model,
                max_tokens=max_tokens,
                provider=provider,
                reasoning_replay=reasoning_replay,
            )
        
        return await client.responses(
            messages=messages,
            input_items=input_items,
            system_prompt=system_prompt,
            tools=tools,
            model=model,
            max_tokens=max_tokens,
            reasoning_replay=reasoning_replay,
        )
    
    def list_models(self, provider: Optional[str] = None) -> List[str]:
        """List available models for a provider."""
        provider = provider or self.default_provider
        if provider in self.providers:
            return self.providers[provider].list_models()
        return []
    
    def get_provider_info(self) -> Dict[str, Any]:
        """Get information about all providers."""
        info = {}
        for name, provider in self.providers.items():
            info[name] = {
                "name": provider.name,
                "default_model": getattr(provider, 'default_model', ''),
                "models": provider.list_models()
            }
        return info
    
    async def check_provider_health(self, provider: str) -> Dict[str, Any]:
        """Check if a provider is available."""
        if provider not in self.providers:
            return {"status": "unknown", "error": "Provider not found"}
        
        if provider == 'ollama':
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.get(f"http://127.0.0.1:11434/api/tags")
                    response.raise_for_status()
                    return {"status": "healthy", "models": [m["name"] for m in response.json().get("models", [])]}
            except Exception as e:
                return {"status": "unhealthy", "error": str(e)}
        
        return {"status": "unknown", "error": "Health check not implemented"}


# Global client instance
llm_client = LLMClient()

# Register for config reload
from src.config import service_reload_manager
service_reload_manager.register('llm', llm_client.reinit)
