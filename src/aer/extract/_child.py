"""The parse sandbox's child process. **Not imported by anything; run as a module.**

Invoked as ``python -m aer.extract._child <extractor>`` by :mod:`aer.extract.sandbox`, with the
document on stdin and one JSON object returned on stdout. Threat T5's isolation boundary is
this process: a parser that hangs, exhausts memory or segfaults takes this down and nothing
else.

**Its imports are deliberately narrow.** Nothing here imports :mod:`aer.config`,
:mod:`aer.db` or anything that touches the network or a database. The child receives bytes and
returns text; giving it a database session or a set of credentials would put both inside the
blast radius it exists to contain.

The limits arrive as arguments rather than being read from configuration, for the same reason.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from aer.extract.html import extract_html

# The extractors this process is allowed to run, by name. A dictionary rather than a lookup by
# attribute, so an argument arriving from anywhere cannot name an arbitrary callable.
_EXTRACTORS = {"html": extract_html}


def _apply_memory_cap(limit_bytes: int) -> bool:
    """Cap the address space this process may claim. Returns whether the cap was applied.

    POSIX only. ``resource`` does not exist on Windows, where the equivalent needs a Job
    Object and a native extension — so on the platform this runs on for local use, the
    wall-clock timeout is the enforced limit and this is not.

    That gap is narrower than it looks. The attacks a memory cap is for are decompression
    bombs, and the two that matter are closed elsewhere: the size ceiling is checked before
    these bytes are handed over, and ``aer.extract.xml`` refuses to resolve entities at all, so
    a billion-laughs document has nothing to expand. The cap is a backstop for the unknown
    case, and its absence is reported rather than assumed.
    """
    try:
        import resource  # noqa: PLC0415 -- POSIX-only, and its absence is the point
    except ImportError:  # pragma: no cover -- exercised on Windows, not in CI
        return False

    resource.setrlimit(resource.RLIMIT_AS, (limit_bytes, limit_bytes))
    return True


def main(argv: list[str]) -> int:
    if len(argv) != 3:  # noqa: PLR2004 -- program name, extractor, memory limit
        print(json.dumps({"ok": False, "error": "usage: _child <extractor> <memory_bytes>"}))
        return 2

    extractor_name, memory_limit = argv[1], int(argv[2])
    capped = _apply_memory_cap(memory_limit)

    extractor = _EXTRACTORS.get(extractor_name)
    if extractor is None:
        _emit({"ok": False, "error": f"unknown extractor {extractor_name!r}"})
        return 2

    data = sys.stdin.buffer.read()

    try:
        extracted = extractor(data)
    except Exception as exc:
        # `type(exc).__name__` rather than the class: the parent must not have to import
        # whatever a parser library raised in order to understand the failure.
        _emit({"ok": False, "error": f"{type(exc).__name__}: {exc}", "memory_capped": capped})
        return 1

    _emit(
        {
            "ok": True,
            "memory_capped": capped,
            "text": extracted.text,
            "extractor": extracted.extractor,
            "extractor_version": extracted.extractor_version,
            "title": extracted.title,
        }
    )
    return 0


def _emit(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload))
    sys.stdout.flush()


if __name__ == "__main__":  # pragma: no cover -- entered as a subprocess
    sys.exit(main(sys.argv))
