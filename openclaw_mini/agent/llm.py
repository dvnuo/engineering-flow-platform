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

    # GitHub Copilot API configuration
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

    def _get_headers(self) -> Dict[str, str]:
        """Get headers for API request based on provider."""
        if self.provider == "github_copilot":
            return {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }
        else:
            return {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }

    def _get_chat_endpoint(self) -> str:
        """Get the chat completions endpoint URL."""
        if self.provider == "github_copilot":
            return f"{self.COPILOT_API_BASE}{self.COPILOT_SUFFIX}"
        return f"{self.api_base}/chat/completions"

    def _get_completions_endpoint(self) -> str:
        """Get the completions endpoint URL."""
        if self.provider == "github_copilot":
            # GitHub Copilot uses chat completions primarily
            return f"{self.COPILOT_API_BASE}{self.COPILOT_SUFFIX}"
        return f"{self.api_base}/completions"

    async def chat(
        self,
        messages: List[Dict[str, str]],
        system_prompt: Optional[str] = None,
    ) -> str:
        """Send a chat request to the LLM with retry logic."""
        # Build messages
        all_messages = []
        if system_prompt:
            all_messages.append({"role": "system", "content": system_prompt})
        all_messages.extend(messages)

        # Prepare request payload
        payload = {
            "model": self.model,
            "messages": all_messages,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
        }

        # Add GitHub Copilot specific headers and parameters
        headers = self._get_headers()
        endpoint = self._get_chat_endpoint()

        # Make API request with retry
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
                    data = await response.json()

                # Extract response content
                choices = data.get("choices", [])
                if choices:
                    content = choices[0].get("message", {}).get("content", "")
                else:
                    content = ""
                return content.strip()

            except (httpx.HTTPStatusError, httpx.RequestError) as e:
                last_error = e
                if attempt < self.max_retries - 1:
                    delay = self.retry_delay * (2 ** attempt)  # Exponential backoff
                    logger.warning(f"API request failed, retrying in {delay}s: {e}")
                    await asyncio.sleep(delay)
                else:
                    logger.error(f"API request failed after {self.max_retries} attempts: {e}")
                    raise

        raise last_error

    async def complete(self, prompt: str) -> str:
        """Simple text completion (non-chat model) with retry logic."""
        payload = {
            "model": self.model,
            "prompt": prompt,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
        }

        headers = self._get_headers()
        endpoint = self._get_completions_endpoint()

        # Make API request with retry
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
                    data = await response.json()

                content = data.get("choices", [{}])[0].get("text", "")
                return content.strip()

            except (httpx.HTTPStatusError, httpx.RequestError) as e:
                last_error = e
                if attempt < self.max_retries - 1:
                    delay = self.retry_delay * (2 ** attempt)  # Exponential backoff
                    logger.warning(f"API request failed, retrying in {delay}s: {e}")
                    await asyncio.sleep(delay)
                else:
                    logger.error(f"API request failed after {self.max_retries} attempts: {e}")
                    raise

        raise last_error

    def is_github_copilot(self) -> bool:
        """Check if current provider is GitHub Copilot."""
        return self.provider == "github_copilot"


# Global LLM client instance
llm_client = LLMClient()
