"""The readiness audit of the equity research tool.

A top-level package, deliberately outside ``src/aer`` and ``tests/``. It drives live runs
through the platform's own services, produces the console-style baseline through the
vendor's SDK directly, and scores both — none of which belongs in the product, and one of
which (the SDK import) the product's boundary tests would rightly refuse inside ``src``.

Nothing here writes a platform row the platform would not have written itself: the driver
approves gates through :func:`aer.services.approvals.record_decision` exactly as the web
route does, and the baseline runner writes no ``agent_runs`` or ``costs`` rows at all,
because it is not a platform call and must not count against the platform's own caps.

The findings document is ``docs/plan/readiness-audit-2026-09.md``.
"""
