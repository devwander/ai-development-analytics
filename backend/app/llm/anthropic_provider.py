"""Anthropic provider, built on the official SDK.

Structured output is enforced by the API (`messages.parse` with a Pydantic
schema), not by parsing prose out of a text response. That is what makes the
"a resposta do LLM deve ser obrigatoriamente estruturada" requirement (§13)
real rather than aspirational.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, TypeVar

from pydantic import BaseModel, ValidationError

from app.llm.base import (
    LLMNotConfigured,
    LLMProvider,
    LLMResponse,
    LLMUnavailable,
    LLMUsage,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    import anthropic

T = TypeVar("T", bound=BaseModel)

DEFAULT_MODEL = "claude-opus-5"


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        client: "anthropic.Anthropic | None" = None,
        effort: str = "medium",
    ) -> None:
        self.model = model
        self.effort = effort
        self._api_key = api_key or None
        self._client = client

    # -- configuration ----------------------------------------------------
    def _has_credentials(self) -> bool:
        """An unset ANTHROPIC_API_KEY does not mean there are no credentials.

        The SDK also resolves ANTHROPIC_AUTH_TOKEN and an `ant auth login`
        profile on disk, so an injected client or either env var counts.
        """
        return bool(
            self._client
            or self._api_key
            or os.environ.get("ANTHROPIC_API_KEY")
            or os.environ.get("ANTHROPIC_AUTH_TOKEN")
        )

    def is_configured(self) -> bool:
        if not self._has_credentials():
            return False
        try:
            import anthropic  # noqa: F401
        except ImportError:
            return False
        return True

    def client(self) -> "anthropic.Anthropic":
        if self._client is not None:
            return self._client
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - packaging issue
            raise LLMNotConfigured(
                "the 'anthropic' package is not installed"
            ) from exc
        if not self._has_credentials():
            raise LLMNotConfigured(
                "no Anthropic credentials found. Set LLM_API_KEY in .env, export "
                "ANTHROPIC_API_KEY, or run 'ant auth login'."
            )
        self._client = (
            anthropic.Anthropic(api_key=self._api_key)
            if self._api_key
            else anthropic.Anthropic()
        )
        return self._client

    # -- completion -------------------------------------------------------
    def complete_structured(
        self,
        *,
        system: str,
        prompt: str,
        schema: type[T],
        max_tokens: int = 4096,
    ) -> LLMResponse[T]:
        import anthropic

        client = self.client()
        try:
            response = client.messages.parse(
                model=self.model,
                max_tokens=max_tokens,
                system=[
                    {
                        "type": "text",
                        "text": system,
                        # The instructions are identical for every session, so
                        # caching them makes a batch run markedly cheaper.
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                thinking={"type": "adaptive"},
                output_config={"effort": self.effort},
                messages=[{"role": "user", "content": prompt}],
                output_format=schema,
            )
        except anthropic.AuthenticationError as exc:
            raise LLMNotConfigured(f"Anthropic rejected the credentials: {exc}") from exc
        except anthropic.PermissionDeniedError as exc:
            raise LLMNotConfigured(f"credentials lack permission: {exc}") from exc
        except anthropic.RateLimitError as exc:
            raise LLMUnavailable(f"rate limited by Anthropic: {exc}") from exc
        except anthropic.APIStatusError as exc:
            raise LLMUnavailable(
                f"Anthropic returned {exc.status_code}: {exc.message}"
            ) from exc
        except anthropic.APIConnectionError as exc:
            raise LLMUnavailable(f"could not reach Anthropic: {exc}") from exc

        if response.stop_reason == "refusal":
            category = getattr(response.stop_details, "category", None)
            raise LLMUnavailable(
                f"the model declined to answer (category: {category})"
            )

        parsed = getattr(response, "parsed_output", None)
        if parsed is None:
            raise LLMUnavailable(
                "the model returned no structured output "
                f"(stop_reason={response.stop_reason})"
            )
        if not isinstance(parsed, schema):
            try:
                parsed = schema.model_validate(parsed)
            except ValidationError as exc:
                raise LLMUnavailable(
                    f"structured output did not match the schema: {exc}"
                ) from exc

        usage = getattr(response, "usage", None)
        return LLMResponse(
            parsed=parsed,
            model=response.model or self.model,
            provider=self.name,
            usage=LLMUsage(
                input_tokens=getattr(usage, "input_tokens", 0) or 0,
                output_tokens=getattr(usage, "output_tokens", 0) or 0,
                cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
                cache_creation_tokens=getattr(usage, "cache_creation_input_tokens", 0)
                or 0,
            ),
            request_id=getattr(response, "_request_id", None),
        )
