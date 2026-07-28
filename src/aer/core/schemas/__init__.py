"""Pydantic schemas: the shape of everything crossing a boundary.

These are the contract between the GUI, the API and the service layer. They live in
``aer.core`` because they are pure declarations — no I/O, no clock, no database — which
means the same schema validates a JSON body, an HTML form submission and a test fixture,
and there is exactly one definition of what a valid research request is.

Rules that need to know the time or the configuration are deliberately **not** expressed
here. See :mod:`aer.core.schemas.request` for how those are handled without letting a
clock read into the correctness core.
"""

from __future__ import annotations
