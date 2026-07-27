"""Application error hierarchy.

Every error carries a stable machine-readable ``code`` and a ``context`` mapping. The
code is what the API surfaces to clients and what tests assert on; the context is
structured detail for logs and for the "technical details" disclosure in the GUI.

Nothing here formats a message for an end user. Presentation is the API layer's job, and
keeping it out of the exception means the same error can be rendered differently in a log
line, an API response and a run console without duplicating logic.
"""

from __future__ import annotations

from typing import Any, ClassVar

__all__ = [
    "AerError",
    "BudgetExceededError",
    "ConfigError",
    "ExternalServiceError",
    "IntegrityError",
    "ValidationError",
]


class AerError(Exception):
    """Base class for every error raised deliberately by this application.

    Attributes:
        code: Stable, machine-readable identifier. Safe to expose to clients and to
            assert on in tests. Never change an existing code without a migration note.
        context: Structured detail about the failure. Must not contain secrets; the
            logging redaction processor is a backstop, not a licence to be careless.
    """

    code: ClassVar[str] = "aer_error"
    http_status: ClassVar[int] = 500

    def __init__(self, message: str, *, context: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.context: dict[str, Any] = dict(context or {})

    def to_dict(self) -> dict[str, Any]:
        """Return a serialisable representation for API responses and structured logs."""
        return {
            "code": self.code,
            "message": self.message,
            "context": self.context,
        }

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code!r}, message={self.message!r})"


class ConfigError(AerError):
    """Configuration is missing, malformed, or internally inconsistent.

    Raised at startup. Should name every problem found, not just the first, so an
    operator can fix a fresh environment in one pass.
    """

    code: ClassVar[str] = "config_error"
    http_status: ClassVar[int] = 500


class ValidationError(AerError):
    """Input failed a domain rule.

    Distinct from :class:`pydantic.ValidationError`, which reports schema violations.
    This one is for rules the schema cannot express, such as an as-of date in the future
    or a security outside the supported universe.
    """

    code: ClassVar[str] = "validation_error"
    http_status: ClassVar[int] = 422


class ExternalServiceError(AerError):
    """An upstream provider failed, timed out, rate-limited us, or returned nonsense.

    Carries enough context to decide whether a retry is worthwhile. Never raised for a
    *successful* response whose content we dislike — that is a data-quality concern and
    belongs in the validation layer.
    """

    code: ClassVar[str] = "external_service_error"
    http_status: ClassVar[int] = 502

    def __init__(
        self,
        message: str,
        *,
        provider: str,
        retryable: bool = False,
        status_code: int | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        merged: dict[str, Any] = {
            "provider": provider,
            "retryable": retryable,
            "status_code": status_code,
        }
        merged.update(context or {})
        super().__init__(message, context=merged)
        self.provider = provider
        self.retryable = retryable
        self.status_code = status_code


class BudgetExceededError(AerError):
    """A run would exceed its cost ceiling.

    This is a control-flow signal, not a crash: the orchestrator pauses the run and waits
    for a human decision rather than failing it. Cost caps that merely warn are caps that
    do not work.
    """

    code: ClassVar[str] = "budget_exceeded"
    http_status: ClassVar[int] = 402


class IntegrityError(AerError):
    """Stored evidence does not match its recorded hash, or an audit chain is broken.

    Always serious. Never retried, never swallowed: it means the evidence base or the
    audit trail can no longer be trusted, which invalidates the reports built on it.
    """

    code: ClassVar[str] = "integrity_error"
    http_status: ClassVar[int] = 500
