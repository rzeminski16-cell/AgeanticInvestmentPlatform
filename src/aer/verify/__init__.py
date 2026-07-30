"""Deterministic checks that decide whether the platform's claims are supported.

Everything in this package answers a question about evidence with code rather than with a
model. That is the point of it existing as a package: the boundary between "proposed" and
"confirmed" is a package boundary, and it is enforced by tests that read the source tree.
"""

from __future__ import annotations

from aer.verify.citations import (
    MATCH_THRESHOLD,
    VERIFICATION_METHOD,
    ReadOnce,
    VerificationOutcome,
    verify,
    verify_job_citations,
)

__all__ = [
    "MATCH_THRESHOLD",
    "VERIFICATION_METHOD",
    "ReadOnce",
    "VerificationOutcome",
    "verify",
    "verify_job_citations",
]
