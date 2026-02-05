"""
LLM client for OpsClaw Mini - Supports multiple providers.

Providers:
- OpenAI (GPT-3.5, GPT-4)
- GitHub Copilot
- Claude (Anthropic)
- Ollama (Local)
"""

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

import httpx

from config import config

logger = logging.getLogger(__name__)


class BaseProvider:
    """Base class for LLM providers."""
    
    def __init__(self, name: str, api_base: str, api_key_env: str = ''):
        self.name = name
        self.api_base = api_base
        self.api_key_env = api_key_env
        self.timeout = 120.0
    
    def _get_headers(self) -> Dict[str, str]:
        """Get request headers."""
        import os
        api_key = os.environ.get(self.api_key_env) if self.api_key_env else ''
        if not api_key:
            api_key = config.llm.get('api_key', '')
        return {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
    
    async def chat(self, **kwargs) -> Dict[str, Any]:
        raise NotImplementedError
    
    def list_models(self) -> List[str]:
        return []
    
    async def _call_api(self, endpoint: str, payload: Dict) -> Dict:
        """Make API call with retry logic."""
        headers = self._get_headers()
        last_error = None
        max_retries = config.llm.get('max_retries', 3)
        retry_delay = config.llm.get('retry_delay', 1)
        
        for attempt in range(max_retries):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await client.post(
                        f"{self.api_base}{endpoint}",
                        headers=headers,
                        json=payload
                    )
                    response.raise_for_status()
                    return response.json()
                    
            except (httpx.HTTPStatusError, httpx.RequestError) as e:
                last_error = e
                if attempt < max_retries - 1:
                    delay = retry_delay * (2 ** attempt)
                    logger.warning(f"API error, retrying in {delay}s: {e}")
                    await asyncio.sleep(delay)
        
        raise last_error


class OpenAIProvider(BaseProvider):
    """OpenAI GPT provider."""
    
    def __init__(self):
        super().__init__(
            name="openai",
            api_base=config.llm.get('api_base', 'https://api.openai.com/v1'),
            api_key_env='OPENCLAW_LLM_API_KEY'
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
    ) -> Dict[str, Any]:
        """Call OpenAI Chat Completions API."""
        all_messages = []
        if system_prompt:
            all_messages.append({"role": "system", "content": system_prompt})
        all_messages.extend(messages)
        
        payload = {
            "model": model or self.default_model,
            "messages": all_messages,
            "temperature": temperature,
            "max_tokens": max_tokens or config.llm.get('max_tokens', 1000),
        }
        
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        
        data = await self._call_api("/chat/completions", payload)
        
        choice = data["choices"][0]
        message = choice["message"]
        
        result = {
            "content": message.get("content", ""),
            "tool_calls": message.get("tool_calls", []),
            "usage": data.get("usage", {}),
        }
        
        # Calculate cost (simplified)
        model_name = data.get("model", "")
        if "gpt-4" in model_name:
            result["usage"]["cost_usd"] = result["usage"].get("total_tokens", 0) * 0.00006
        else:
            result["usage"]["cost_usd"] = result["usage"].get("total_tokens", 0) * 0.000002
        
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
            api_base="https://api.github.com/copilot",
            api_key_env='GITHUB_COPILOT_TOKEN'
        )
        self.default_model = config.llm.get('model', 'gpt-4')
    
    async def chat(
        self,
        messages: List[Dict],
        system_prompt: Optional[str] = None,
        tools: Optional[List[Dict]] = None,
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Call GitHub Copilot Chat API."""
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
            "messages": all_messages,
            "model": model or self.default_model,
        }
        
        # Add tools support (similar to OpenAI)
        if tools:
            payload["tools"] = tools
        
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.api_base}/chat/completions",
                headers=headers,
                json=payload
            )
            response.raise_for_status()
            data = response.json()
        
        # Parse response - check for tool calls
        message_data = data.get("choices", [{}])[0].get("message", {})
        content = message_data.get("content", "")
        
        # GitHub Copilot may return tool_calls
        tool_calls = message_data.get("tool_calls", [])
        
        # Calculate usage (approximate)
        prompt_tokens = sum(len(str(m).split()) for m in all_messages) * 4  # Rough estimate
        completion_tokens = len(content.split()) * 4  # Rough estimate
        
        return {
            "content": content,
            "tool_calls": tool_calls,
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            }
        }
    
    def list_models(self) -> List[str]:
        return ["gpt-4", "gpt-4-turbo"]


class ClaudeProvider(BaseProvider):
    """Anthropic Claude provider."""
    
    def __init__(self):
        super().__init__(
            name="claude",
            api_base="https://api.anthropic.com",
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
    ) -> Dict[str, Any]:
        """Call Anthropic Claude API."""
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
        
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.api_base}/messages",
                headers=self._get_headers(),
                json=payload
            )
            response.raise_for_status()
            data = response.json()
        
        return self._parse_response(data)
    
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
        """Parse Claude response."""
        content_blocks = data.get("content", [])
        text_content = ""
        tool_calls = []
        
        for block in content_blocks:
            if block.get("type") == "text":
                text_content = block.get("text", "")
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
        return {
            "content": text_content,
            "tool_calls": tool_calls,
            "usage": {
                "prompt_tokens": usage.get("input_tokens", 0),
                "completion_tokens": usage.get("output_tokens", 0),
                "total_tokens": usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
            }
        }
    
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
            api_base="http://127.0.0.1:11434",
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
        
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(
                    f"{self.api_base}/api/chat",
                    json=payload
                )
                response.raise_for_status()
                data = response.json()
        except httpx.ConnectError:
            return {
                "content": "",
                "tool_calls": [],
                "error": "Ollama not running. Start with: ollama serve",
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
            }
        
        return self._parse_response(data)
    
    def _parse_response(self, data: Dict) -> Dict[str, Any]:
        """Parse Ollama response."""
        message = data.get("message", {})
        content = message.get("content", "")
        
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
        
        return {
            "content": content,
            "tool_calls": tool_calls,
            "usage": {
                "prompt_tokens": data.get("prompt_eval_count", 0),
                "completion_tokens": data.get("eval_count", 0),
                "total_tokens": data.get("prompt_eval_count", 0) + data.get("eval_count", 0),
            }
        }
    
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
    ) -> Dict[str, Any]:
        """Chat with LLM."""
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
