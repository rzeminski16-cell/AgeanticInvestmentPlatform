"""Reading structlog events out of ``caplog``.

``aer.logging`` bridges structlog onto stdlib logging, so what reaches a ``LogRecord`` is
not a formatted string: ``record.msg`` is the structlog event dictionary, and the
traceback for an exception has already been rendered into its ``exception`` key by
``format_exc_info``. That means ``record.exc_info`` is ``None`` even for a log call that
passed one, and asserting on it would quietly always fail.

Asserting on the event dictionary is also simply better: a test that checks
``event["request_id"] == "trace-me"`` cannot pass by accident the way a substring search
over ``caplog.text`` can.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any

__all__ = ["events_at_or_above", "structlog_events"]


def structlog_events(records: list[logging.LogRecord]) -> Iterator[dict[str, Any]]:
    """Yield the structlog event dictionary from each record that carries one."""
    for record in records:
        if isinstance(record.msg, dict):
            yield record.msg


def events_at_or_above(records: list[logging.LogRecord], level: int) -> Iterator[dict[str, Any]]:
    for record in records:
        if record.levelno >= level and isinstance(record.msg, dict):
            yield record.msg
