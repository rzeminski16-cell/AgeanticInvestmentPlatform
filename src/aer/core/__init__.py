"""Correctness core: pure domain types and logic.

This package and ``aer.calc`` are checked under ``mypy --strict``. Everything here must be
**pure and free of side effects** — no I/O, no network, no database, no filesystem, no
global mutable state, and no reading of the clock. A caller passes values in and gets
values out.

That constraint is not stylistic. These modules encode the rules the platform's
correctness rests on — units, dates, point-in-time admissibility, citation identity — and
purity is what makes them exhaustively testable without fixtures, mocks or infrastructure.
The moment one of them reaches for `datetime.now()` or a database session, the property
tests that guard it stop being able to reach every branch.

Configuration lives in ``aer.config`` rather than here precisely because it reads the
environment.
"""

from __future__ import annotations
