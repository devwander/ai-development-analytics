"""The contract for LLM output.

Every field here is INFERRED. The response is validated against this model, so
free-form prose can never become a stored metric (brief §13).

Nothing in this schema duplicates something the collectors already measure.
Token counts, durations, tool counts and file lists are observed data, and
asking the model to restate them would invite it to contradict the record
(brief §31: never use the LLM for metrics obtainable directly from the data).
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class TaskType(str, Enum):
    FEATURE = "feature"
    BUG_FIX = "bug_fix"
    DEBUGGING = "debugging"
    REFACTORING = "refactoring"
    TESTING = "testing"
    DOCUMENTATION = "documentation"
    RESEARCH = "research"
    CONFIGURATION = "configuration"
    DEPLOYMENT = "deployment"
    CODE_REVIEW = "code_review"
    OTHER = "other"


class Complexity(str, Enum):
    TRIVIAL = "trivial"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    UNKNOWN = "unknown"


class AnalysisResult(BaseModel):
    """Structured judgement about one session."""

    objective: str = Field(
        description="What the developer set out to achieve, in one or two sentences."
    )
    summary: str = Field(description="What actually happened, in 2-4 sentences.")
    task_type: TaskType
    complexity: Complexity
    result: str | None = Field(
        default=None,
        description=(
            "How the session ended. Null when the transcript does not show an "
            "outcome - do not invent a conclusion."
        ),
    )
    success: bool | None = Field(
        default=None,
        description="Null when the transcript does not show whether the goal was met.",
    )
    requires_rework: bool = Field(
        description=(
            "True only when the transcript shows work being redone: a correction "
            "requested, a change reverted, the same thing implemented twice, a "
            "rejected solution, or failing tests forcing new edits. Message volume "
            "alone is never rework."
        )
    )
    rework_evidence: list[str] = Field(
        default_factory=list,
        description=(
            "One concrete, quotable observation per item. Must be empty when "
            "requires_rework is false, and non-empty when it is true."
        ),
    )
    technologies: list[str] = Field(
        default_factory=list,
        description="Languages, frameworks and tools the session actually worked with.",
    )
    problems_encountered: list[str] = Field(
        default_factory=list,
        description="Concrete obstacles visible in the transcript. Empty if none.",
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "How well the supplied evidence supports this judgement. Lower it when "
            "the digest was heavily truncated or the session is ambiguous."
        ),
    )


class AnalysisRejected(ValueError):
    """The model returned something that violates the contract."""


def validate_result(result: AnalysisResult) -> AnalysisResult:
    """Enforce the invariants a JSON schema cannot express.

    The rework flag and its evidence must agree. A ``requires_rework: true`` with
    no evidence is exactly the unfounded conclusion the brief forbids (§12), so
    it is rejected rather than stored.
    """
    if result.requires_rework and not result.rework_evidence:
        raise AnalysisRejected(
            "requires_rework is true but rework_evidence is empty; "
            "rework must be justified by concrete observations"
        )
    if not result.requires_rework and result.rework_evidence:
        raise AnalysisRejected(
            "rework_evidence was provided but requires_rework is false"
        )
    return result
