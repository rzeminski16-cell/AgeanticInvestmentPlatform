"""The calculation kernel: every number the platform produces comes from here.

**No language model may ever produce a figure that bypasses this package.** That is the
organising rule of the whole codebase, restated at the module that enforces it. A
discounted cash flow is forty lines of Python with unit tests; it is not a reasoning task,
and putting arithmetic in prose is the single most common way systems like this produce
confidently wrong numbers.

Three properties hold here and nowhere else in the codebase quite so strictly.

**Pure.** No I/O, no globals, no clock reads, no database. ``mypy --strict``. A function
here can be tested by calling it, which is why the correctness core is the one part of the
system with no excuses about being hard to test.

**Unit-safe.** Every value carries a unit through every operation. Adding dollars to
pounds raises; it never coerces, and it never silently succeeds because both happened to
be ``Decimal``. Dividing dollars by shares produces dollars-per-share, and the type system
knows it.

**Traced.** Every calculation records its formula, its inputs — each with a unit *and* a
source — the code version that produced it, and its output. A figure with no such record
cannot reach a report, because there is nowhere else for a figure to come from.

``Decimal`` throughout, never ``float``. Binary floating point cannot represent 0.1
exactly, and a research platform that silently rounds cash flows is worthless.
"""

from __future__ import annotations
