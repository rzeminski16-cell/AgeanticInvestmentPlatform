"""Whether a skill applies to a run, decided from what is known at plan time.

Pure and exhaustive on purpose: every input arrives as an argument — the request's market
and mode, the ticker, whatever sector classification is already known — so the whole
matrix of scope and applicability is testable without a database, and the answer always
carries its reason, because a skill silently absent from a plan is a skill whose author
files a bug.

**A mismatch is a skip, never an error** (§2.12): the skill stays enabled, the plan
records ``skipped_not_applicable`` with the reason, and a run it does apply to picks it
up unchanged.

**Unknown is not a match.** A sector-scoped skill against a company whose classification
is not yet known does not run on hope — it is skipped with a reason saying exactly that.
The conservative reading costs a section; the optimistic one runs analysis its author
scoped away from this kind of company. Sector *exclusions* read the other way for the same
reason: an exclusion only fires on a classification somebody actually holds, because
skipping on an unknown would quietly disable a global skill for every first-time company.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

__all__ = ["UK_EXCHANGES", "ApplicabilityDecision", "market_of", "skill_applies"]

# The one UK venue the universe supports; everything else supported is a US listing.
# Derived from `aer.core.universe.SUPPORTED_EXCHANGES`, restated rather than imported so
# a new exchange must be classified here deliberately instead of defaulting to a market.
UK_EXCHANGES: Final[frozenset[str]] = frozenset({"LSE"})


def market_of(exchange: str) -> str:
    """US or UK, from the request's exchange."""
    return "UK" if exchange.upper() in UK_EXCHANGES else "US"


@dataclass(frozen=True, slots=True)
class ApplicabilityDecision:
    """Applies, or does not — and if not, exactly why, in a sentence for the plan."""

    applicable: bool
    reason: str = ""


def skill_applies(  # noqa: PLR0911 -- one return per rule is the readable shape
    *,
    scope: str,
    markets: tuple[str, ...],
    analysis_modes: tuple[str, ...],
    exclude_sectors: tuple[str, ...],
    ticker: str,
    market: str,
    analysis_mode: str,
    sector_profile_keys: frozenset[str],
) -> ApplicabilityDecision:
    """The plan-time applicability decision for one skill.

    Args:
        sector_profile_keys: The specialist profiles the company's known classification
            suggests — empty when nothing is known yet, which is every first-time company
            at plan time.
    """
    if scope == "run":
        return ApplicabilityDecision(
            applicable=False,
            reason=(
                "Run-scoped skills are attached to a run explicitly; they are never "
                "selected automatically."
            ),
        )

    if scope.startswith("company:"):
        wanted = scope.removeprefix("company:")
        if wanted != ticker:
            return ApplicabilityDecision(
                applicable=False,
                reason=f"Scoped to {wanted}; this run researches {ticker}.",
            )

    if scope.startswith("sector:"):
        wanted = scope.removeprefix("sector:")
        if not sector_profile_keys:
            return ApplicabilityDecision(
                applicable=False,
                reason=(
                    f"Scoped to the {wanted} sector, and this company's classification "
                    "is not yet known. A sector-scoped skill does not run on hope."
                ),
            )
        if wanted not in sector_profile_keys:
            return ApplicabilityDecision(
                applicable=False,
                reason=(
                    f"Scoped to the {wanted} sector; this company's classification "
                    f"suggests {', '.join(sorted(sector_profile_keys))}."
                ),
            )

    if market not in markets:
        return ApplicabilityDecision(
            applicable=False,
            reason=f"Applies in {', '.join(markets)}; this is a {market} listing.",
        )

    if analysis_modes and analysis_mode not in analysis_modes:
        return ApplicabilityDecision(
            applicable=False,
            reason=(
                f"Applies to {', '.join(analysis_modes)} analysis; this run is {analysis_mode}."
            ),
        )

    excluded = sector_profile_keys & frozenset(exclude_sectors)
    if excluded:
        return ApplicabilityDecision(
            applicable=False,
            reason=(
                f"Excluded for the {', '.join(sorted(excluded))} sector, which this "
                "company's classification suggests."
            ),
        )

    return ApplicabilityDecision(applicable=True)
