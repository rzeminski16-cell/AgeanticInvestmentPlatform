"""Shared pytest fixtures.

Deliberately minimal at this stage. Two things will be added here as the codebase grows,
and are noted now so they are not forgotten:

* A socket-blocking autouse fixture, so the default suite provably cannot reach the
  network (arrives with the HTTP fetch layer). Until then the "no network in tests" rule
  in ``CLAUDE.md`` is a convention rather than an enforced invariant.
* A transactional database session fixture bound to the Docker Compose Postgres, skipped
  when the service is unavailable (arrives with the database layer).
"""

from __future__ import annotations

import pytest


@pytest.fixture
def fake_anthropic_key() -> str:
    """A syntactically plausible but entirely fake Anthropic key.

    Used to prove that redaction works. It must never be a real key, and it must look
    enough like one to exercise the matching patterns.
    """
    return "sk-ant-api03-FAKEFAKEFAKE"
