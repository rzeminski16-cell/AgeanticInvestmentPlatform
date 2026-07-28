"""Point-in-time fact selection: what was known, as at a date.

This is the most important forty lines in the ingestion layer, so the rule is stated
before the code.

**The rule.** Group facts by what they are statements *about* — concept, unit, period end
and fiscal period. Within a group, discard every fact filed after the as-of date. From
what remains, choose the one filed **latest**. That is the most recent thing the company
had said about that period as at that date, which is precisely what an analyst working
that day would have had in front of them.

**The rule that is wrong, and why it is tempting.** Taking the latest value regardless of
filing date is one line shorter and gives cleaner-looking data, because restatements
resolve accounting messes and the restated figure is usually the more "correct" one. It is
also look-ahead bias in its purest form. A model tested on restated history sees the
company's 2020 results as they were understood in 2023 — including the reclassifications
that were made *because of* what happened in 2021 and 2022. It will appear to have
predicted things it could not have known, and the live version will not.

That failure is silent. Nothing raises, no number looks implausible, and the backtest
simply looks better than reality. It is the single most common way a research system
produces confident nonsense, which is why the selection is deterministic Python with an
exhaustive test rather than a judgement made anywhere near a prompt.

**Every fact is accounted for.** The result is a partition: each input fact appears
exactly once, in ``chosen`` or in ``rejected`` with a reason. A selector that returned only
its winners would make "why is this figure not in the report?" unanswerable, and that
question gets asked about every report.
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
    "FILED_AFTER_AS_OF_DATE",
    "SUPERSEDED_BY_LATER_FILING",
    "PointInTimeSelection",
    "RejectedFact",
    "select_point_in_time",
]

FILED_AFTER_AS_OF_DATE: Final = "filed_after_as_of_date"
"""The fact did not exist yet. Using it would be look-ahead bias."""

SUPERSEDED_BY_LATER_FILING: Final = "superseded_by_later_filing"
"""A later filing, still on or before the as-of date, restated this period."""

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

    # Set when the reason is supersession: which filing won. Makes the rejection
    # auditable without re-running the selection to work out what beat it.
    superseded_by: str | None = None


@dataclass(frozen=True, slots=True)
class PointInTimeSelection:
    """The outcome of a selection: a partition of the input facts."""

    chosen: tuple[RawFact, ...]
    rejected: tuple[RejectedFact, ...]
    as_of_date: date
    basis: FactBasis

    @property
    def rejected_for_look_ahead(self) -> tuple[RejectedFact, ...]:
        """Facts excluded because they were filed after the as-of date."""
        return tuple(r for r in self.rejected if r.reason == FILED_AFTER_AS_OF_DATE)

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


def select_point_in_time(
    facts: tuple[RawFact, ...] | list[RawFact],
    *,
    as_of_date: date,
    basis: FactBasis = FactBasis.AS_REPORTED,
) -> PointInTimeSelection:
    """Choose the facts that were public as at ``as_of_date``.

    Args:
        facts: Every observation the source reported, unfiltered.
        as_of_date: The date the research is performed *as at*. A fact filed on this exact
            date is included — a filing accepted on a day was public that day.
        basis: Which version of each number to select. Only
            :attr:`~aer.core.enums.FactBasis.AS_REPORTED` is implemented.

    Raises:
        ValidationError: If a basis other than ``AS_REPORTED`` is requested.

    Returns:
        A partition of the input: every fact appears exactly once, in ``chosen`` or in
        ``rejected`` with a reason.
    """
    if basis is not FactBasis.AS_REPORTED:
        # Deliberately not implemented rather than merely unused. Selecting the restated
        # figure means selecting information that did not exist at the as-of date, and a
        # convenience function for doing that is a convenience function for introducing
        # look-ahead bias. If a genuine need appears, it needs an ADR, not a branch here.
        message = (
            f"Only the {FactBasis.AS_REPORTED.value} basis is implemented. Selecting "
            f"{basis.value} facts would use figures published after the as-of date, "
            "which is look-ahead bias by construction."
        )
        raise ValidationError(
            message, context={"basis": basis.value, "as_of_date": as_of_date.isoformat()}
        )

    groups: dict[tuple[str, str, date, str | None], list[RawFact]] = defaultdict(list)
    rejected: list[RejectedFact] = []

    for fact in facts:
        if fact.filed_date > as_of_date:
            rejected.append(RejectedFact(fact=fact, reason=FILED_AFTER_AS_OF_DATE))
            continue
        groups[fact.period_key].append(fact)

    chosen: list[RawFact] = []
    for candidates in groups.values():
        # Sorted rather than max() so the losers are identified as well as the winner.
        #
        # Three keys, each doing a distinct job:
        #
        # * `filed_date` is the rule itself -- the most recent thing the company had said.
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

    return PointInTimeSelection(
        chosen=tuple(sorted(chosen, key=lambda f: (f.concept, f.period_end, f.unit))),
        rejected=tuple(rejected),
        as_of_date=as_of_date,
        basis=basis,
    )
