"""Structured logging with secret redaction.

Logs are JSON by default so they can be shipped to any collector without parsing rules.
Every log line passes through :func:`redact_secrets`, which masks values two ways:

1. **By field name** — any key whose name looks like a credential.
2. **By value shape** — anything matching a known credential format, wherever it appears,
   including nested inside a message string or a deeply nested payload.

The two are complementary on purpose. Name-based masking catches a well-named field
holding an unrecognised secret format; value-based masking catches a secret that ended up
in a field nobody thought to name carefully, which is how credentials actually leak.

Note on field names: a bare ``key`` is deliberately *not* treated as sensitive. This
schema uses ``key`` extensively for non-secret identifiers — ``step_key``, ``section_key``,
``idempotency_key``, ``skills.key`` — and masking those would gut the debuggability of
the run console for no security benefit. Genuine credential fields are caught by the
more specific fragments below, and by value-shape matching regardless of their name.

**A bare ``token`` is excluded for the same reason, and the reason is sharper.** This
platform meters spend in tokens: ``input_tokens``, ``output_tokens``, ``cache_read_tokens``,
``max_tokens``. A substring match on "token" masked every one of them, so ``provider.completed``
— the line the £100/month budget is reconciled from — reported its token counts as
``***REDACTED***``, and a failure telling the operator to raise a ceiling hid the ceiling.
Credential tokens are named in compound (``access_token``, ``refresh_token``) and are matched
that way; a field named exactly ``token`` is matched exactly. No integer is a credential, and
the plural is never one either.

Redaction is a backstop, not a licence: do not put secrets in log context in the first
place.
"""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import Mapping, MutableMapping
from typing import Any, Final

import structlog

__all__ = [
    "MASK",
    "configure_logging",
    "get_logger",
    "is_sensitive_name",
    "redact_secrets",
    "redact_value",
]

MASK: Final = "***REDACTED***"

_MAX_DEPTH: Final = 12

# Marks the root handler this module installs, so reconfiguring replaces our own handler
# without disturbing anyone else's. Removing every root handler -- the obvious
# implementation -- silently unhooks pytest's log capture and anything a host process
# attached before calling us, which is not ours to do.
_HANDLER_TAG: Final = "_aer_logging_handler"

# Substrings that mark a field name as holding a credential. Deliberately specific --
# see the module docstring for why bare "key" and bare "token" are both excluded.
_SENSITIVE_NAME_FRAGMENTS: Final[tuple[str, ...]] = (
    "access_token",
    "api_key",
    "apikey",
    "auth_token",
    "authorization",
    "bearer_token",
    "credential",
    "csrf_token",
    "id_token",
    "passwd",
    "password",
    "private_key",
    "pwd",
    "refresh_token",
    "secret",
    "session_id",
    "session_token",
    "token_secret",
)

# Names that are credentials on their own, but whose *substring* would swallow the token
# counts this platform meters spend with. Matched whole, never as a fragment.
_SENSITIVE_NAMES: Final[frozenset[str]] = frozenset({"token"})

# Value shapes that are credentials wherever they appear. Each entry is a
# (pattern, replacement) pair; the replacement may keep a non-secret prefix so a log line
# still shows *what kind* of credential was present.
_SECRET_VALUE_PATTERNS: Final[tuple[tuple[re.Pattern[str], str], ...]] = (
    (re.compile(r"sk-ant-[A-Za-z0-9_\-]{4,}"), MASK),
    (re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._\-]{8,}"), r"\1" + MASK),
    (re.compile(r"(?i)\b(basic\s+)[A-Za-z0-9+/=]{8,}"), r"\1" + MASK),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"), MASK),
    (re.compile(r"\bsk-[A-Za-z0-9]{20,}"), MASK),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}"), MASK),
)


def is_sensitive_name(name: str) -> bool:
    """Return whether a field name looks like it holds a credential."""
    lowered = name.lower()
    if lowered in _SENSITIVE_NAMES:
        return True
    return any(fragment in lowered for fragment in _SENSITIVE_NAME_FRAGMENTS)


def _redact_string(value: str) -> str:
    for pattern, replacement in _SECRET_VALUE_PATTERNS:
        value = pattern.sub(replacement, value)
    return value


def redact_value(value: Any, *, _depth: int = 0) -> Any:
    """Recursively redact secrets from an arbitrary value.

    Mappings, sequences and strings are traversed; everything else is returned unchanged.
    Recursion is depth-limited so a pathological or cyclic structure cannot hang the
    logging pipeline -- losing detail in a log line is always preferable to wedging the
    process that writes it.
    """
    if _depth >= _MAX_DEPTH:
        return value

    if isinstance(value, str):
        return _redact_string(value)

    if isinstance(value, Mapping):
        redacted: dict[Any, Any] = {}
        for key, item in value.items():
            if isinstance(key, str) and is_sensitive_name(key):
                redacted[key] = MASK
            else:
                redacted[key] = redact_value(item, _depth=_depth + 1)
        return redacted

    if isinstance(value, (bytes, bytearray)):
        return value

    # bytes/bytearray are sequences too, but are not meaningfully traversable here.
    if isinstance(value, (list, tuple, set, frozenset)):
        rebuilt = [redact_value(item, _depth=_depth + 1) for item in value]
        return type(value)(rebuilt)

    return value


def _redact_mapping(mapping: Mapping[str, Any], *, depth: int) -> dict[str, Any]:
    redacted: dict[str, Any] = {}
    for key, item in mapping.items():
        redacted[key] = MASK if is_sensitive_name(key) else redact_value(item, _depth=depth)
    return redacted


def redact_secrets(
    _logger: Any,
    _method_name: str,
    event_dict: MutableMapping[str, Any],
) -> dict[str, Any]:
    """structlog processor that masks credentials across the whole event dictionary.

    The parameter is a ``MutableMapping`` rather than a ``dict`` because that is what
    structlog's ``Processor`` protocol specifies; narrowing it breaks the contravariance
    the protocol requires.
    """
    return _redact_mapping(event_dict, depth=0)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a bound structlog logger."""
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger


def configure_logging(
    *,
    level: str = "INFO",
    json_output: bool = True,
) -> None:
    """Configure structlog on top of the standard library logging module.

    Routing through stdlib logging rather than structlog's own printer is deliberate:
    FastAPI, uvicorn, httpx and SQLAlchemy all log through stdlib, and bridging means
    their output is formatted *and redacted* by the same pipeline. A third-party client
    logging a request URL that carries a token is a realistic leak path, and it is only
    closed if foreign records go through redaction too.

    Args:
        level: Minimum level name, e.g. ``"INFO"`` or ``"DEBUG"``.
        json_output: Emit JSON when true, human-readable console output when false.
            Defaults to JSON everywhere, including development, so what you debug against
            has the same shape as what you ship.

    Raises:
        ValueError: If ``level`` is not a recognised level name.
    """
    numeric_level = logging.getLevelNamesMapping().get(level.upper())
    if numeric_level is None:
        message = f"Unknown log level: {level!r}"
        raise ValueError(message)

    # Applied to structlog events and to foreign stdlib records alike, so both end up
    # with the same fields.
    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
    ]

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.processors.format_exc_info,
            # Redact here as well as in the formatter below. Cheap, idempotent, and it
            # means a caller who bypasses the stdlib handler still gets masking.
            redact_secrets,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    renderer: Any = (
        structlog.processors.JSONRenderer()
        if json_output
        else structlog.dev.ConsoleRenderer(colors=False)
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=[*shared_processors, structlog.stdlib.ExtraAdder()],
        processors=[
            # The single choke point every record passes through, structlog or foreign.
            redact_secrets,
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    setattr(handler, _HANDLER_TAG, True)

    root_logger = logging.getLogger()
    for existing in list(root_logger.handlers):
        if getattr(existing, _HANDLER_TAG, False):
            root_logger.removeHandler(existing)
    root_logger.addHandler(handler)
    root_logger.setLevel(numeric_level)
