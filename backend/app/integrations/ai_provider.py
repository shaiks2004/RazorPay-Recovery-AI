from __future__ import annotations

import json
from typing import Protocol
import httpx
from app.domain.ai_advisor import AIAdvice, RecoveryCasePacket

PROMPT_VERSION = "ai-advisor-v1"
SYSTEM_PROMPT = """You are RECOVER's revenue recovery advisory model. Analyze only supplied structured facts. You advise only: never execute payments, create links, approve actions, override policy, change state, access credentials, or contact customers. Policy is deterministic and authoritative. Do not invent facts. Return only the required JSON schema."""

class AIAdvisor(Protocol):
    provider: str
    model: str
    def advise(self, packet: RecoveryCasePacket) -> AIAdvice: ...

class AdvisorUnavailable(Exception): pass

class OpenAICompatibleAdvisor:
    """HTTP-only adapter; it has no database, Razorpay, or tool capability."""
    provider = "OPENAI_COMPATIBLE"
    def __init__(self, *, base_url: str, api_key: str, model: str, timeout_seconds: float, max_output_tokens: int) -> None:
        self._base_url, self._api_key, self.model, self._timeout, self._max = base_url, api_key, model, timeout_seconds, max_output_tokens
    def advise(self, packet: RecoveryCasePacket) -> AIAdvice:
        try:
            response = httpx.post(self._base_url.rstrip("/") + "/chat/completions", headers={"Authorization": f"Bearer {self._api_key}"}, json={"model": self.model, "response_format": {"type": "json_object"}, "max_tokens": self._max, "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": json.dumps(packet.__dict__, sort_keys=True, separators=(",", ":"))}]}, timeout=self._timeout)
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            if not isinstance(content, str) or len(content) > 8192: raise AdvisorUnavailable("AI_RESPONSE_TOO_LARGE")
            return AIAdvice.from_dict(json.loads(content))
        except (httpx.HTTPError, KeyError, TypeError, json.JSONDecodeError, ValueError) as error:
            raise AdvisorUnavailable(type(error).__name__) from error

class FakeAIAdvisor:
    provider = "FAKE"
    def __init__(self, advice: AIAdvice, model: str = "fake-v1") -> None: self._advice, self.model = advice, model
    def advise(self, packet: RecoveryCasePacket) -> AIAdvice: return self._advice
