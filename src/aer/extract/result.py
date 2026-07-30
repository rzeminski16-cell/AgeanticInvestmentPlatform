"""What one extraction produced: the text, and what was noticed about the document.

Two things travel together because they are made together and separately because they answer
different questions.

:class:`~aer.core.schemas.extraction.ExtractedText` is the evidence surface — every citation's
locator points into it, and its hash is what tells a verifier whether the extractor has changed
underneath a stored locator. Findings are an observation *about* the document, and they
deliberately do not affect that hash: a new heuristic noticing something extra must not
invalidate every citation recorded before the heuristic existed.

They are combined here, in the extract package, rather than in ``core``. ``Finding`` needs a
``Locator`` and ``ExtractedText`` lives beside ``Locator``, so putting findings on the text
itself would make the two core modules import each other. The dependency runs one way instead:
locators, then findings, then this.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from aer.core.schemas.extraction import ExtractedText
from aer.core.schemas.injection import Finding, InjectionSignal

__all__ = ["ExtractedDocument"]


@dataclass(frozen=True, slots=True)
class ExtractedDocument:
    """An extraction and its findings."""

    text: ExtractedText
    findings: tuple[Finding, ...] = field(default_factory=tuple)

    @property
    def is_flagged(self) -> bool:
        """Whether anything was noticed. **Not** whether the document is dangerous.

        A flag is for a human at gate 2; nothing in the platform refuses a document on the
        strength of one. See :mod:`aer.extract.injection` for why that is the design rather
        than a gap in it.
        """
        return bool(self.findings)

    def signals(self) -> frozenset[InjectionSignal]:
        """The distinct kinds noticed, for a badge that says what rather than how many."""
        return frozenset(finding.signal for finding in self.findings)
