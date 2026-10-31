"""LLM provider adapters.

The model is only ever asked to produce JSON matching a schema. It receives no tools, so it
cannot execute commands or change any system; everything it returns is validated before use.
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod

from app.config import Settings, get_settings

log = logging.getLogger("tracemind.llm")


class LLMError(RuntimeError):
    """Raised for any provider failure; callers fall back to the deterministic writer."""


class LLMProvider(ABC):
    name: str = "abstract"

    @property
    @abstractmethod
    def model_version(self) -> str: ...

    @abstractmethod
    def generate_json(self, system: str, user: str, schema: dict) -> dict: ...


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, settings: Settings) -> None:
        import anthropic  # imported lazily so the app runs without the provider configured

        self._anthropic = anthropic
        self.settings = settings
        kwargs: dict = {"timeout": settings.llm_timeout_seconds, "max_retries": 2}
        if settings.llm_api_key:
            kwargs["api_key"] = settings.llm_api_key
        if settings.llm_base_url:
            kwargs["base_url"] = settings.llm_base_url
        self.client = anthropic.Anthropic(**kwargs)

    @property
    def model_version(self) -> str:
        return f"anthropic/{self.settings.llm_model}"

    def generate_json(self, system: str, user: str, schema: dict) -> dict:
        a = self._anthropic
        try:
            response = self.client.beta.messages.create(
                model=self.settings.llm_model,
                max_tokens=self.settings.llm_max_output_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config={"effort": self.settings.llm_effort, "format": {"type": "json_schema", "schema": schema}},
                # Route policy declines to a fallback model chosen server-side.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except a.RateLimitError as exc:
            raise LLMError("provider rate limit reached") from exc
        except a.APITimeoutError as exc:
            raise LLMError("provider request timed out") from exc
        except a.APIStatusError as exc:
            raise LLMError(f"provider returned HTTP {exc.status_code}") from exc
        except a.APIConnectionError as exc:
            raise LLMError("could not reach provider") from exc

        if response.stop_reason == "refusal":
            raise LLMError("provider declined the request")
        if response.stop_reason == "max_tokens":
            raise LLMError("provider output was truncated")
        text = next((b.text for b in response.content if b.type == "text"), None)
        if not text:
            raise LLMError("provider returned no text")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise LLMError("provider returned invalid JSON") from exc
        if not isinstance(data, dict):
            raise LLMError("provider returned a non-object")
        return data


class StaticProvider(LLMProvider):
    """Test double: returns canned outputs (or raises) without any network access."""

    name = "static"

    def __init__(self, outputs: list[dict | Exception], model: str = "static/test") -> None:
        self.outputs = list(outputs)
        self._model = model
        self.calls: list[tuple[str, str]] = []

    @property
    def model_version(self) -> str:
        return self._model

    def generate_json(self, system: str, user: str, schema: dict) -> dict:
        self.calls.append((system, user))
        if not self.outputs:
            raise LLMError("no canned output left")
        item = self.outputs.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


_override: LLMProvider | None = None


def set_provider_override(provider: LLMProvider | None) -> None:
    global _override
    _override = provider


def get_provider() -> LLMProvider | None:
    """Return the configured provider, or None when running deterministic-only."""
    if _override is not None:
        return _override
    settings = get_settings()
    if settings.llm_provider == "anthropic":
        try:
            return AnthropicProvider(settings)
        except Exception as exc:  # missing SDK or credentials: degrade, never crash
            log.warning("anthropic provider unavailable: %s", exc)
            return None
    return None
