"""Workflow definitions: what a run does, as an ordered list of steps.

A workflow is data about which functions run in which order. It contains no branching on
company type, no per-section dispatch, and no section keys — the sections a run produces
come from ``section_definitions``, so a new section changes what a run does without
changing what a workflow *is*.

Versioned by name (``vertical_slice_v1``). A run records which workflow version produced
it, so a report can be reproduced against the sequence of steps that actually made it
rather than the sequence that exists today.
"""

from __future__ import annotations
