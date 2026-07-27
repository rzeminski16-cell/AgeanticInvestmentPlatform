"""Ageiantic Equity Research Platform.

A local-first, auditable equity research platform for UK and US listed equities.

The organising principle of this codebase, stated once here and enforced throughout:
**deterministic Python owns every number and every fact; the language model owns
planning, interpretation, comparison, adversarial challenge and writing.** Retrieval,
parsing, arithmetic, date handling, citation verification, rendering and cost metering
are all ordinary, testable code. See ``docs/adr/0003``.
"""

from __future__ import annotations

from aer.version import __version__, build_identity, git_sha, version

__all__ = ["__version__", "build_identity", "git_sha", "version"]
