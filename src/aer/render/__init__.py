"""Turning a report's stored sections into a document.

Rendering is deliberately the last thing that happens and the least clever. By the time
anything here runs, every figure is a persisted calculation and every fact a hashed
artefact; this layer's only job is to lay them out and attach the footnotes that lead back.

**Nothing here decides what a report says.** A renderer that computed a figure, or omitted
a section it judged uninteresting, would put a decision somewhere nobody would look for it.
"""

from __future__ import annotations
