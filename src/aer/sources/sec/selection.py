"""Fact selection: one observation per thing a filer said, the most recent word winning.

This is the most important forty lines in the ingestion layer, so the rule is stated
before the code.

**The rule.** Group facts by what they are statements *about* — concept, unit, period end
and fiscal period. Within a group, choose the one filed **latest**. That is the most recent
thing the company has said about that period, which is what an analyst reading the filings
as they stand today has in front of them (ADR 0113: a run reads the filings as they stand).

**Why the losers are kept.** The result is a partition: each input fact appears exactly
once, in ``chosen`` or in ``rejected`` with a reason and the accession that beat it. A
selector that returned only its winners would make "why is this figure not in the report?"
unanswerable, and that question gets asked about every report. A restatement is the
ordinary case — the later filing's figure wins and the original is recorded as superseded
by it — so the audit trail says which filing every number came from and which it replaced.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Final

from aer.core.enums import FactBasis
from aer.core.schemas.facts import RawFact
from aer.errors import ValidationError

__all__ = [
    "DUPLICATE_TAGGING_IN_SAME_FILING",
    "SUPERSEDED_BY_LATER_FILING",
    "FactSelection",
    "RejectedFact",
    "select_latest",
]

SUPERSEDED_BY_LATER_FILING: Final = "superseded_by_later_filing"
"""A later filing restated this period; the later figure is the one chosen."""

DUPLICATE_TAGGING_IN_SAME_FILING: Final = "duplicate_tagging_in_same_filing"
"""One filing tagged the same concept twice, under two names.

Common in a taxonomy-transition year: a filer moving to ASC 606 tags revenue as both
``Revenues`` and ``RevenueFromContractWithCustomerExcludingAssessedTax``, reporting one
number under two labels. Nothing was superseded — the two arrived together — so calling it
supersession would put a false statement in the audit trail.
"""


@dataclass(frozen=True, slots=True)
class RejectedFact:
    """A fact that was not selected, and why."""

    fact: RawFact
    reason: str

    # Which filing won. Makes the rejection auditable without re-running the selection to
    # work out what beat it.
    superseded_by: str | None = None


@dataclass(frozen=True, slots=True)
class FactSelection:
    """The outcome of a selection: a partition of the input facts."""

    chosen: tuple[RawFact, ...]
    rejected: tuple[RejectedFact, ...]
    basis: FactBasis

    def latest(self, concept: str, *, unit: str | None = None) -> RawFact | None:
        """The chosen fact for a concept with the most recent period end."""
        candidates = [
            fact
            for fact in self.chosen
            if fact.concept == concept and (unit is None or fact.unit == unit)
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda f: (f.period_end, f.filed_date))

    def for_concept(self, concept: str) -> tuple[RawFact, ...]:
        """Every chosen fact for a concept, oldest period first."""
        return tuple(
            sorted(
                (fact for fact in self.chosen if fact.concept == concept),
                key=lambda f: f.period_end,
            )
        )


def select_latest(
    facts: tuple[RawFact, ...] | list[RawFact],
    *,
    basis: FactBasis = FactBasis.AS_REPORTED,
) -> FactSelection:
    """Choose, for each thing the filer reported, the figure from the latest filing.

    Args:
        facts: Every observation the source reported, unfiltered.
        basis: Which version of each number to select. Only
            :attr:`~aer.core.enums.FactBasis.AS_REPORTED` is implemented: the figure as the
            winning filing reported it, with no vendor standardisation applied.

    Raises:
        ValidationError: If a basis other than ``AS_REPORTED`` is requested.

    Returns:
        A partition of the input: every fact appears exactly once, in ``chosen`` or in
        ``rejected`` with a reason.
    """
    if basis is not FactBasis.AS_REPORTED:
        # Deliberately not implemented rather than merely unused. A vendor-standardised
        # figure is a figure nobody filed, and a fact that traces to no filing has no
        # artefact behind it (invariant 1). If a genuine need appears, it needs an ADR, not
        # a branch here.
        message = (
            f"Only the {FactBasis.AS_REPORTED.value} basis is implemented. A {basis.value} "
            "figure is one no filing reported, so nothing archived could stand behind it."
        )
        raise ValidationError(message, context={"basis": basis.value})

    groups: dict[tuple[str, str, date, str | None, str | None, str | None], list[RawFact]] = (
        defaultdict(list)
    )
    for fact in facts:
        groups[fact.period_key].append(fact)

    chosen: list[RawFact] = []
    rejected: list[RejectedFact] = []
    for candidates in groups.values():
        # Sorted rather than max() so the losers are identified as well as the winner.
        #
        # Three keys, each doing a distinct job:
        #
        # * `filed_date` is the rule itself -- the most recent thing the company has said.
        # * `accession` breaks a same-day tie. A 10-K and a same-day 10-K/A are ordered by
        #   the sequence number EDGAR issued, and the later one is the more recent word.
        # * `raw_concept` breaks a tie *within one filing*, which happens whenever a filer
        #   tags one number under two names. Without it the winner would depend on the
        #   order the parser happened to walk the taxonomy in, and a reproducible research
        #   platform cannot have a result that depends on dictionary ordering.
        ordered = sorted(candidates, key=lambda f: (f.filed_date, f.accession, f.raw_concept))
        winner = ordered[-1]
        chosen.append(winner)
        rejected.extend(
            RejectedFact(
                fact=loser,
                # A fact from the winner's own filing was not superseded by anything: the
                # two arrived together. Recording it as supersession would put a false
                # statement in the audit trail.
                reason=(
                    DUPLICATE_TAGGING_IN_SAME_FILING
                    if loser.accession == winner.accession
                    else SUPERSEDED_BY_LATER_FILING
                ),
                superseded_by=winner.accession,
            )
            for loser in ordered[:-1]
        )

    return FactSelection(
        chosen=tuple(sorted(chosen, key=lambda f: (f.concept, f.period_end, f.unit))),
        rejected=tuple(rejected),
        basis=basis,
    )
