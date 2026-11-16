"""Provider-agnostic LLM interface.

The brief requires that the system is not coupled to a single vendor (§5). The
contract here is deliberately narrow: given a system prompt, a user prompt and
a Pydantic schema, return a validated instance of that schema. Nothing about
tool use, streaming or vendor message formats leaks past this boundary, so
adding a provider means implementing one method.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    """Any failure that prevented a usable, schema-valid answer."""


class LLMNotConfigured(LLMError):
    """The provider exists but has no credentials or is missing configuration."""


class LLMUnavailable(LLMError):
    """The provider could not be reached, or refused the request."""


@dataclass
class LLMUsage:
    """What the call cost. Observed from the provider's own response."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_tokens
            + self.cache_creation_tokens
        )


@dataclass
class LLMResponse[TModel: BaseModel]:
    parsed: TModel
    model: str
    provider: str
    usage: LLMUsage = field(default_factory=LLMUsage)
    #: Present when the provider reports one; useful for support tickets.
    request_id: str | None = None


class LLMProvider(ABC):
    """One method, one job: structured completion."""

    #: Stable identifier stored alongside every analysis.
    name: str = "base"

    @abstractmethod
    def is_configured(self) -> bool:
        """Whether a call could succeed right now, without making one."""

    @abstractmethod
    def complete_structured(
        self,
        *,
        system: str,
        prompt: str,
        schema: type[T],
        max_tokens: int = 4096,
    ) -> LLMResponse[T]:
        """Return an instance of ``schema``, or raise an ``LLMError``.

        Implementations must never return unvalidated text, and must never
        fabricate a result when the provider fails - a raised error is the
        correct outcome, because the objective collection does not depend on
        this succeeding.
        """
