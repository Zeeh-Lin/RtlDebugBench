# Copyright (c) 2024 Bytedance Ltd. and/or its affiliates
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.

"""Reusable OpenAI-compatible LLM client with retry and JSON parsing."""

import json
import os
import re
import threading
import time
from typing import Any


# Common fences produced by chat models.
_JSON_FENCE_RE = re.compile(
    r"^```(?:json)?\s*\n(.*?)\n```$",
    re.DOTALL | re.IGNORECASE,
)


def strip_markdown_fences(text: str) -> str:
    """Remove Markdown JSON fences if present."""
    text = text.strip()
    match = _JSON_FENCE_RE.match(text)
    if match:
        return match.group(1).strip()
    return text


class LLMClient:
    """Thin wrapper around the OpenAI Python client.

    Defaults mirror the S1 setup so that S2 can reuse the same API key and
    endpoint configuration. All LLM calls are retried with exponential backoff.
    """

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        api_key_env: str | None = None,
        max_retries: int = 3,
        base_delay: float = 1.0,
    ):
        self.base_url = base_url or "https://api.deepseek.com"
        self.model = model or "deepseek-reasoner"
        self.api_key_env = api_key_env or "DEEPSEEK_API_KEY"
        self.max_retries = max_retries
        self.base_delay = base_delay
        self._client: Any | None = None
        self._client_lock = threading.Lock()

    @property
    def client(self) -> Any:
        """Lazily build and cache the OpenAI client (thread-safe)."""
        if self._client is None:
            with self._client_lock:
                if self._client is None:
                    from openai import OpenAI

                    api_key = os.environ.get(self.api_key_env)
                    if not api_key:
                        raise ValueError(
                            f"LLM API key environment variable not set: {self.api_key_env}"
                        )
                    self._client = OpenAI(api_key=api_key, base_url=self.base_url)
        return self._client

    def call(
        self,
        prompt: str,
        response_format: dict[str, str] | None = None,
        temperature: float = 1.0,
    ) -> str:
        """Call the LLM and return the raw text content.

        Retries on API or network errors with exponential backoff.
        """
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
        }
        if response_format:
            kwargs["response_format"] = response_format

        last_exception: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                response = self.client.chat.completions.create(**kwargs)
                content = response.choices[0].message.content or ""
                return content.strip()
            except Exception as exc:
                last_exception = exc
                if attempt == self.max_retries - 1:
                    break
                wait = self.base_delay * (2 ** attempt)
                time.sleep(wait)

        raise RuntimeError(
            f"LLM call failed after {self.max_retries} retries: {last_exception}"
        )

    def call_json(self, prompt: str, temperature: float = 1.0) -> dict:
        """Call the LLM with JSON-object response format and parse the result.

        Retries on both API failures and JSON parse errors.
        """
        last_exception: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                content = self.call(
                    prompt,
                    response_format={"type": "json_object"},
                    temperature=temperature,
                )
                cleaned = strip_markdown_fences(content)
                return json.loads(cleaned)
            except (json.JSONDecodeError, RuntimeError) as exc:
                last_exception = exc
                if attempt == self.max_retries - 1:
                    break
                wait = self.base_delay * (2 ** attempt)
                time.sleep(wait)

        raise RuntimeError(
            f"LLM JSON call failed after {self.max_retries} retries: {last_exception}"
        )
