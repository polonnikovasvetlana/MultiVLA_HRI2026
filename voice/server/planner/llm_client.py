from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any


class LLMError(RuntimeError):
    pass


class OpenAICompatibleClient:
    def __init__(self) -> None:
        self.base_url = os.getenv(
            "LLM_BASE_URL",
            "http://127.0.0.1:11434/v1",
        ).rstrip("/")

        self.model = os.getenv(
            "LLM_MODEL",
            "qwen3-vl:8b",
        )

        self.api_key = os.getenv("LLM_API_KEY", "ollama")
        self.timeout = float(os.getenv("LLM_TIMEOUT_SECONDS", "60"))
        self.max_tokens = int(os.getenv("LLM_MAX_TOKENS", "800"))
        self.reasoning_effort = os.getenv("LLM_REASONING_EFFORT", "none")

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.model)

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        if not self.configured:
            raise LLMError("LLM_BASE_URL and LLM_MODEL are not configured")
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.0,
            "max_tokens": self.max_tokens,
            # Qwen thinking is enabled by default in Ollama.  For this small,
            # schema-bound planning request it can consume the whole token
            # budget before message.content is produced.
            "reasoning_effort": self.reasoning_effort,
            "response_format": {"type": "json_object"},
        }
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body: dict[str, Any] = json.load(response)
            return str(body["choices"][0]["message"]["content"])
        except (urllib.error.URLError, KeyError, IndexError, ValueError) as error:
            raise LLMError(f"LLM request failed: {error}") from error
