"""Provider selection.

Only Anthropic is implemented. OpenAI and a local endpoint are named here
because the brief lists them (§5), but they are registered as explicit
failures rather than untested code: shipping an unverifiable client that looks
functional is worse than a clear "not implemented", because the first sign of
trouble would be a wrong analysis stored as data.
"""

from __future__ import annotations

from typing import Callable

from app.config import Settings
from app.llm.base import LLMNotConfigured, LLMProvider


def _anthropic(settings: Settings) -> LLMProvider:
    from app.llm.anthropic_provider import AnthropicProvider

    return AnthropicProvider(
        api_key=settings.llm_api_key or None,
        model=settings.llm_model,
        effort=settings.llm_effort,
    )


def _not_implemented(name: str) -> Callable[[Settings], LLMProvider]:
    def build(settings: Settings) -> LLMProvider:
        raise LLMNotConfigured(
            f"the '{name}' provider is not implemented yet. "
            "Set LLM_PROVIDER=anthropic, or implement app/llm/"
            f"{name}_provider.py against the LLMProvider interface."
        )

    return build


BUILDERS: dict[str, Callable[[Settings], LLMProvider]] = {
    "anthropic": _anthropic,
    "openai": _not_implemented("openai"),
    "local": _not_implemented("local"),
}


def available_providers() -> list[str]:
    return sorted(BUILDERS)


def build_provider(settings: Settings) -> LLMProvider:
    name = (settings.llm_provider or "").strip().lower()
    builder = BUILDERS.get(name)
    if builder is None:
        raise LLMNotConfigured(
            f"unknown LLM_PROVIDER={settings.llm_provider!r}. "
            f"Known providers: {', '.join(available_providers())}"
        )
    return builder(settings)
