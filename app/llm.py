"""Minimal OpenAI-compatible chat client (works with Ollama, Groq, OpenAI, LM Studio...).

Every call is best-effort: on any failure it returns None and callers fall back
to deterministic heuristics. The LLM is never trusted to make money decisions.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

import httpx

log = logging.getLogger("agentpay.llm")
_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


def extract_json(text: str) -> Optional[dict[str, Any]]:
    """Parse a JSON object from model output, tolerating ```json fences or chatter."""
    if not text:
        return None
    text = text.strip()
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    m = _JSON_BLOCK.search(text)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


class LLMClient:
    def __init__(self, base_url: str = "", model: str = "", api_key: str = "",
                 timeout: float = 45.0, transport: Optional[httpx.BaseTransport] = None):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key or "none"
        self.timeout = timeout
        self._transport = transport
        self.last_error: Optional[str] = None

    @property
    def enabled(self) -> bool:
        return bool(self.base_url and self.model)

    @property
    def label(self) -> str:
        return f"{self.model} @ {self.base_url}" if self.enabled else "heuristic (no LLM configured)"

    def chat(self, system: str, user: str, json_mode: bool = False, temperature: float = 0.1) -> Optional[str]:
        if not self.enabled:
            return None
        body: dict[str, Any] = {
            "model": self.model,
            "temperature": temperature,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        try:
            with httpx.Client(timeout=self.timeout, transport=self._transport) as c:
                r = c.post(f"{self.base_url}/chat/completions", json=body,
                           headers={"Authorization": f"Bearer {self.api_key}"})
                r.raise_for_status()
                content = r.json()["choices"][0]["message"]["content"]
                self.last_error = None
                return content
        except Exception as e:  # network, HTTP, schema: all non-fatal
            self.last_error = f"{type(e).__name__}: {e}"
            log.warning("LLM call failed: %s", self.last_error)
            return None

    def chat_json(self, system: str, user: str) -> Optional[dict[str, Any]]:
        return extract_json(self.chat(system, user, json_mode=True) or "")
