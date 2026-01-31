"""LLM client for OpenClaw Mini."""

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

import httpx

from openclaw_mini.config import config

logger = logging.getLogger(__name__)


class LLMClient:
    """Simple LLM client supporting OpenAI-compatible APIs."""

    def __init__(self):
        self.provider = config.llm.get("provider", "openai")
        self.api_base = config.llm.get("api_base", "https://api.openai.com/v1")
        self.api_key = config.llm.get("api_key", "")
        self.model = config.llm.get("model", "gpt-3.5-turbo")
        self.max_tokens = config.llm.get("max_tokens", 1000)
        self.temperature = config.llm.get("temperature", 0.7)
        self.max_retries = config.llm.get("max_retries", 3)
        self.retry_delay = config.llm.get("retry_delay", 1)

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

        # Make API request with retry
        last_error = None
        for attempt in range(self.max_retries):
            try:
                async with httpx.AsyncClient(timeout=120.0) as client:
                    response = await client.post(
                        f"{self.api_base}/chat/completions",
                        headers={
                            "Authorization": f"Bearer {self.api_key}",
                            "Content-Type": "application/json",
                        },
                        json=payload,
                    )
                    response.raise_for_status()
                    data = response.json()

                # Extract response content
                content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
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

        # Make API request with retry
        last_error = None
        for attempt in range(self.max_retries):
            try:
                async with httpx.AsyncClient(timeout=120.0) as client:
                    response = await client.post(
                        f"{self.api_base}/completions",
                        headers={
                            "Authorization": f"Bearer {self.api_key}",
                            "Content-Type": "application/json",
                        },
                        json=payload,
                    )
                    response.raise_for_status()
                    data = response.json()

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


# Global LLM client instance
llm_client = LLMClient()
