"""LLM client for OpenClaw Mini."""

import json
from typing import Any, Dict, List, Optional

import httpx

from openclaw_mini.config import config


class LLMClient:
    """Simple LLM client supporting OpenAI-compatible APIs."""

    def __init__(self):
        self.provider = config.llm.get("provider", "openai")
        self.api_base = config.llm.get("api_base", "https://api.openai.com/v1")
        self.api_key = config.llm.get("api_key", "")
        self.model = config.llm.get("model", "gpt-3.5-turbo")
        self.max_tokens = config.llm.get("max_tokens", 1000)
        self.temperature = config.llm.get("temperature", 0.7)

    async def chat(
        self,
        messages: List[Dict[str, str]],
        system_prompt: Optional[str] = None,
    ) -> str:
        """Send a chat request to the LLM."""
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

        # Make API request
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

    async def complete(self, prompt: str) -> str:
        """Simple text completion (non-chat model)."""
        payload = {
            "model": self.model,
            "prompt": prompt,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
        }

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


# Global LLM client instance
llm_client = LLMClient()
