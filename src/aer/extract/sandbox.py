"""Running a parser where it cannot hurt anything else.

Three controls, in the order they apply, and the order is the design:

1. **The size ceiling, before the bytes are handed over.** A decompression bomb does its damage
   *during* parsing, so a size check afterwards measures the crater. This is the only one of the
   three that costs nothing and works everywhere.
2. **Content sniffing, before the extractor is chosen.** ``Content-Type`` is a claim by whoever
   served the file. See :mod:`aer.extract.sniff`.
3. **A separate process with a wall-clock timeout and, where the platform allows it, an address-
   space cap.** A parser that hangs, allocates without limit or segfaults takes the child down
   and nothing else.

**Why a subprocess at all, given the cost.** Parsing is the one operation in this platform where
untrusted bytes drive a large third-party parser — ``lexbor`` for HTML, ``pdfminer.six`` and
``pypdfium2`` for PDF. A segfault in a C extension is not an exception a caller can catch, and an
unbounded loop in a pure-Python parser is not one either; in-process, both take the worker with
them, and with it the run and the run's uncommitted state. The isolation is what makes "the
extract step failed" a recoverable outcome rather than a dead worker.

The cost is real and worth stating: on Windows a process spawn plus a fresh interpreter import
is of the order of a hundred milliseconds. Against a filing that takes a second to parse and a
model call that takes a minute, it does not register.

**The boundary is one async function.** No call site knows a process is involved, which is what
let the PDF extractor arrive without re-inventing any of this.
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any, Final

import structlog

from aer.core.schemas.extraction import ExtractedTable, ExtractedText, PageMap
from aer.core.schemas.injection import Finding
from aer.extract.errors import (
    DocumentTooLargeError,
    MediaTypeMismatchError,
    ParseFailedError,
    ParseTimeoutError,
    UnextractableError,
)
from aer.extract.result import ExtractedDocument
from aer.extract.sniff import DetectedType, sniff

__all__ = ["EXTRACTOR_MEDIA_TYPES", "extract_in_sandbox"]

_log = structlog.get_logger("aer.extract.sandbox")

# Which detected types each extractor may be given. The check is what turns a served-as-HTML
# zip archive into a refusal instead of a bomb.
#
# `UNKNOWN` is absent from every entry, deliberately. A document nothing can classify is one
# nobody has a reason to trust, and "try the HTML parser and see" is how an extractor ends up
# reporting binary noise as prose.
EXTRACTOR_MEDIA_TYPES: Final[dict[str, frozenset[DetectedType]]] = {
    # XHTML and inline XBRL are served as XML and are HTML for reading purposes, so both are
    # admissible here; `sniff` already prefers HTML when the markers are present.
    "html": frozenset({DetectedType.HTML, DetectedType.XML}),
    # Only the real thing. `%PDF-` is an unambiguous signature, so there is no second type worth
    # admitting and every reason not to: handing the PDF parser something that is not a PDF is
    # precisely the case this module exists to prevent.
    "pdf": frozenset({DetectedType.PDF}),
    # Task 45: structured API responses (XBRL facts) become citable evidence, so their
    # excerpts need the same re-read-and-verify path as prose. Only detected JSON — the
    # extractor's own parse then confirms the claim inside the sandbox.
    "json": frozenset({DetectedType.JSON}),
}

# How long to wait for a killed child to actually die before giving up on it.
_REAP_SECONDS: Final = 5.0

# Errors the child reports that mean something specific, mapped back to their own class. A
# child cannot raise across the process boundary, so this is how a diagnosis survives it.
_CHILD_ERRORS: Final[dict[str, type[UnextractableError]]] = {
    "UnextractableError": UnextractableError,
}


async def extract_in_sandbox(
    data: bytes,
    *,
    extractor: str,
    max_bytes: int,
    timeout_seconds: float,
    memory_limit_bytes: int,
) -> ExtractedDocument:
    """Extract text from ``data`` in a child process.

    Args:
        extractor: A key of :data:`EXTRACTOR_MEDIA_TYPES`. Named rather than passed as a
            callable, so nothing a document influences can select what runs.

    Raises:
        DocumentTooLargeError: Over ``max_bytes``, refused before parsing.
        MediaTypeMismatchError: The content is not a kind this extractor may read.
        ParseTimeoutError: The child did not finish in time and was killed.
        UnextractableError: The document parsed and holds no text.
        ParseFailedError: Anything else — the child's own reason is carried on the error.
    """
    allowed = EXTRACTOR_MEDIA_TYPES.get(extractor)
    if allowed is None:
        message = f"There is no {extractor!r} extractor."
        raise ParseFailedError(message, context={"extractor": extractor})

    _refuse_if_too_large(data, extractor=extractor, max_bytes=max_bytes)
    detected = _refuse_if_wrong_kind(data, extractor=extractor, allowed=allowed)

    result = await _run_child(
        data,
        extractor=extractor,
        timeout_seconds=timeout_seconds,
        memory_limit_bytes=memory_limit_bytes,
    )

    if not result.get("ok"):
        raise _child_failure(result, extractor=extractor, detected=detected)

    pages = result.get("pages")
    document = ExtractedDocument(
        text=ExtractedText(
            text=str(result["text"]),
            extractor=str(result["extractor"]),
            extractor_version=str(result["extractor_version"]),
            title=result.get("title"),
        ),
        findings=tuple(Finding.model_validate(f) for f in result.get("findings", [])),
        pages=None if pages is None else PageMap.model_validate(pages),
        tables=tuple(ExtractedTable.model_validate(t) for t in result.get("tables", [])),
    )

    _log.debug(
        "extract.completed",
        extractor=document.text.extractor,
        extractor_version=document.text.extractor_version,
        detected=detected.value,
        input_bytes=len(data),
        characters=len(document.text.text),
        memory_capped=bool(result.get("memory_capped")),
    )
    if document.is_flagged:
        # At `warning`, because a document that tried something is worth seeing in a log even
        # when nobody is looking at the page that shows it.
        _log.warning(
            "extract.flagged",
            signals=sorted(s.value for s in document.signals()),
            findings=len(document.findings),
        )
    return document


def _refuse_if_too_large(data: bytes, *, extractor: str, max_bytes: int) -> None:
    if len(data) <= max_bytes:
        return
    message = (
        f"The document is {len(data):,} bytes, over the {max_bytes:,}-byte parse ceiling. "
        "Refused before parsing, because a decompression bomb does its damage during the parse "
        "and a check afterwards would be measuring the damage."
    )
    raise DocumentTooLargeError(
        message, context={"extractor": extractor, "bytes": len(data), "max_bytes": max_bytes}
    )


def _refuse_if_wrong_kind(
    data: bytes, *, extractor: str, allowed: frozenset[DetectedType]
) -> DetectedType:
    detected = sniff(data)
    if detected in allowed:
        return detected

    message = (
        f"These bytes look like {detected.value}, which the {extractor!r} extractor may not "
        f"read (it accepts {', '.join(sorted(t.value for t in allowed))}). The type is decided "
        "from the content, never from a Content-Type header."
    )
    raise MediaTypeMismatchError(
        message, context={"extractor": extractor, "detected": detected.value}
    )


async def _run_child(
    data: bytes,
    *,
    extractor: str,
    timeout_seconds: float,
    memory_limit_bytes: int,
) -> dict[str, Any]:
    """Start the child, feed it, and get its answer or kill it."""
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "aer.extract._child",
        extractor,
        str(memory_limit_bytes),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(input=data), timeout=timeout_seconds
        )
    except TimeoutError as exc:
        await _kill(process)
        message = (
            f"The {extractor!r} parser did not finish within {timeout_seconds:g}s and was "
            "killed. A small, pathological document can take unbounded time, which is why "
            "this is a clock rather than a byte count."
        )
        raise ParseTimeoutError(
            message, context={"extractor": extractor, "timeout_seconds": timeout_seconds}
        ) from exc

    if not stdout:
        # No JSON at all: the child died before it could answer. A segfault in the parser
        # library looks exactly like this, and it is the case the isolation exists for.
        message = (
            f"The {extractor!r} parser process exited with code {process.returncode} without "
            "returning anything. The parser most likely crashed."
        )
        raise ParseFailedError(
            message,
            context={
                "extractor": extractor,
                "returncode": process.returncode,
                "stderr": _tail(stderr),
            },
        )

    try:
        parsed: dict[str, Any] = json.loads(stdout)
    except json.JSONDecodeError as exc:
        message = f"The {extractor!r} parser process returned output that was not JSON."
        raise ParseFailedError(
            message, context={"extractor": extractor, "stdout": _tail(stdout)}
        ) from exc

    return parsed


async def _kill(process: asyncio.subprocess.Process) -> None:
    """Kill the child and wait for it, so a timeout does not leak a process per document."""
    if process.returncode is not None:
        return
    process.kill()
    try:
        await asyncio.wait_for(process.wait(), timeout=_REAP_SECONDS)
    except TimeoutError:  # pragma: no cover -- a child that survives SIGKILL
        _log.warning("extract.child_would_not_die", pid=process.pid)


def _child_failure(
    result: dict[str, Any], *, extractor: str, detected: DetectedType
) -> ExtractionFailure:
    """Turn the child's reported error back into the right exception class.

    The child reports ``"UnextractableError: ..."`` as text because an exception cannot cross a
    process boundary. Recovering the class matters: "this filing is a scan" needs OCR and "this
    filing is malformed" needs a human, and a caller that saw one generic failure for both
    could not tell a reviewer which.
    """
    reason = str(result.get("error", "no reason given"))
    name = reason.split(":", 1)[0].strip()
    context = {"extractor": extractor, "detected": detected.value, "reason": reason}

    specific = _CHILD_ERRORS.get(name)
    if specific is not None:
        return specific(reason.split(":", 1)[-1].strip(), context=context)

    return ParseFailedError(f"The {extractor!r} parser failed: {reason}", context=context)


def _tail(stream: bytes, *, limit: int = 500) -> str:
    """The end of a child's output, decoded loosely and bounded.

    Bounded because this reaches a log line and a problem detail, and a parser that failed
    noisily can produce megabytes. Decoded with ``replace`` because it is diagnostic text from
    a process that was handling hostile input and may not be valid UTF-8.
    """
    return stream[-limit:].decode("utf-8", errors="replace").strip()


# Named for readability in `_child_failure`'s signature; both are ExtractionError subclasses.
type ExtractionFailure = UnextractableError | ParseFailedError
