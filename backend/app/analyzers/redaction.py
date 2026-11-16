"""Strip identifying details before a digest leaves the machine.

The brief requires that transcripts are never sent out automatically (§19) and
that anonymisation be possible. Analysis is opt-in, but once it is on, the
digest travels to a third party - so redaction is on by default and has to be
turned off deliberately.

What is redacted is chosen to keep the analysis useful: the home directory
prefix goes (it carries the username) while the project-relative path stays,
because "frontend/src/features/patients/PatientListView.tsx" is exactly the
signal the model needs and "/home/<someone>/" is not.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
#: Credentials embedded in a URL: scheme://user:pass@host
_URL_CREDENTIALS_RE = re.compile(r"(?<=://)[^/\s:@]+:[^/\s@]+(?=@)")
#: Long opaque strings that look like API keys or tokens.
_SECRET_RE = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{16,}|glpat-[A-Za-z0-9_-]{16,}"
    r"|AKIA[0-9A-Z]{16}|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})\b"
)


@dataclass
class RedactionReport:
    replacements: dict[str, int] = field(default_factory=dict)

    def note(self, kind: str, count: int) -> None:
        if count:
            self.replacements[kind] = self.replacements.get(kind, 0) + count

    @property
    def total(self) -> int:
        return sum(self.replacements.values())

    def describe(self) -> str:
        if not self.replacements:
            return "no redactions were needed"
        parts = ", ".join(f"{kind}={count}" for kind, count in sorted(self.replacements.items()))
        return f"{self.total} value(s) redacted ({parts})"


class Redactor:
    """Deterministic, order-independent redaction.

    ``home`` is passed in rather than read from the environment so the behaviour
    is testable and so a container's home does not silently differ from the
    host's.
    """

    def __init__(self, home: str | Path | None = None, *, enabled: bool = True) -> None:
        self.enabled = enabled
        self.home = str(Path(home).resolve()) if home else None

    def __call__(self, text: str) -> tuple[str, RedactionReport]:
        report = RedactionReport()
        if not self.enabled or not text:
            return text, report

        # Secrets first: an API key must never survive a later rule reshaping it.
        text, count = _SECRET_RE.subn("[REDACTED_SECRET]", text)
        report.note("secret", count)

        text, count = _URL_CREDENTIALS_RE.subn("[REDACTED_CREDENTIALS]", text)
        report.note("url_credentials", count)

        text, count = _EMAIL_RE.subn("[REDACTED_EMAIL]", text)
        report.note("email", count)

        if self.home:
            # Keep the path relative to home - the structure is the useful part.
            text, count = re.subn(re.escape(self.home + "/"), "~/", text)
            report.note("home_path", count)
            text, count = re.subn(re.escape(self.home), "~", text)
            report.note("home_path", count)

        return text, report
