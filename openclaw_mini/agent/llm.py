"""LLM client for OpenClaw Mini."""

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

import httpx

from openclaw_mini.config import config

logger = logging.getLogger(__name__)


class LLMClient:
    """Simple LLM client supporting OpenAI-compatible APIs and GitHub Copilot."""

    COPILOT_API_BASE = "https://api.github.com/copilot"
    COPILOT_SUFFIX = "/chat/completions"

    def __init__(self):
        self.provider = config.llm.get("provider", "openai")
        self.api_base = config.llm.get("api_base", "https://api.openai.com/v1")
        self.api_key = config.llm.get("api_key", "")
        self.model = config.llm.get("model", "gpt-3.5-turbo")
        self.max_tokens = config.llm.get("max_tokens", 1000)
        self.temperature = config.llm.get("temperature", 0.7)
        self.max_retries = config.llm.get("max_retries", 3)
        self.retry_delay = config.llm.get("retry_delay", 1)
        logger.info(f"LLMClient initialized: provider={self.provider}, model={self.model}")

    def _get_headers(self) -> Dict[str, str]:
        if self.provider == "github_copilot":
            return {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _get_chat_endpoint(self) -> str:
        if self.provider == "github_copilot":
            return f"{self.COPILOT_API_BASE}{self.COPILOT_SUFFIX}"
        return f"{self.api_base}/chat/completions"

    def _get_completions_endpoint(self) -> str:
        if self.provider == "github_copilot":
            return f"{self.COPILOT_API_BASE}{self.COPILOT_SUFFIX}"
        return f"{self.api_base}/completions"

    def _supports_tools(self) -> bool:
        if self.model == "gpt-3.5-turbo":
            return False
        if "-0301" in self.model or "-0314" in self.model:
            return False
        return True

    async def chat(
        self,
        messages: List[Dict[str, str]],
        system_prompt: Optional[str] = None,
        tools: Optional[List[Dict]] = None,
    ) -> Dict[str, Any]:
        """Send a chat request to the LLM with retry logic."""
        all_messages = []
        if system_prompt:
            all_messages.append({"role": "system", "content": system_prompt})
        all_messages.extend(messages)

        payload = {
            "model": self.model,
            "messages": all_messages,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
        }

        use_tools = tools and self._supports_tools()
        if use_tools:
            payload["tools"] = tools
            logger.debug(f"Using tools: {json.dumps(tools, indent=2)[:500]}")

        headers = self._get_headers()
        endpoint = self._get_chat_endpoint()

        last_error = None
        for attempt in range(self.max_retries):
            try:
                async with httpx.AsyncClient(timeout=120.0) as client:
                    logger.debug(f"Sending to {endpoint}, model={self.model}")
                    response = await client.post(
                        endpoint,
                        headers=headers,
                        json=payload,
                    )
                    response.raise_for_status()
                    data = response.json()

                choices = data.get("choices", [])
                if choices:
                    message = choices[0].get("message", {})
                    content = message.get("content", "")
                    tool_calls = message.get("tool_calls", [])
                else:
                    content = ""
                    tool_calls = []

                return {
                    "content": content.strip() if content else "",
                    "tool_calls": tool_calls,
                }

            except (httpx.HTTPStatusError, httpx.RequestError) as e:
                error_body = ""
                if hasattr(e, "response") and e.response is not None:
                    error_body = e.response.text[:500]
                    logger.error(f"API Error 400 details: {error_body}")
                
                if use_tools and "400" in str(e):
                    logger.warning(f"Tool call failed with {self.model}, retrying without tools")
                    payload.pop("tools", None)
                    use_tools = False
                    continue
                
                last_error = e
                if attempt < self.max_retries - 1:
                    delay = self.retry_delay * (2 ** attempt)
                    logger.warning(f"API request failed, retrying in {delay}s: {e}")
                    await asyncio.sleep(delay)
                else:
                    logger.error(f"API request failed after {self.max_retries} attempts")
                    raise

        raise last_error

    async def complete(self, prompt: str) -> str:
        payload = {
            "model": self.model,
            "prompt": prompt,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
        }

        headers = self._get_headers()
        endpoint = self._get_completions_endpoint()

        last_error = None
        for attempt in range(self.max_retries):
            try:
                async with httpx.AsyncClient(timeout=120.0) as client:
                    response = await client.post(
                        endpoint,
                        headers=headers,
                        json=payload,
                    )
                    response.raise_for_status()
                    data = response.json()

                content = data.get("choices", [{}])[0].get("text", "")
                return content.strip()

            except (httpx.HTTPStatusError, httpx.RequestError) as e:
                last_error = e
                if attempt < self.max_retries - 1:
                    delay = self.retry_delay * (2 ** attempt)
                    logger.warning(f"API request failed, retrying in {delay}s: {e}")
                    await asyncio.sleep(delay)
                else:
                    logger.error(f"API request failed after {self.max_retries} attempts: {e}")
                    raise

        raise last_error

    def is_github_copilot(self) -> bool:
        return self.provider == "github_copilot"


llm_client = LLMClient()
