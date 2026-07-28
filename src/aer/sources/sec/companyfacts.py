"""Every XBRL fact an entity ever tagged, parsed into :class:`RawFact` rows.

``data.sec.gov/api/xbrl/companyfacts/CIK##########.json`` is the richest free source of US
fundamentals there is, and its structure encodes the thing that makes it valuable::

    facts
      us-gaap
        Revenues
          units
            USD
              - {start, end, val, accn, fy, fp, form, filed, frame}
              - {start, end, val, accn, fy, fp, form, filed, frame}

Four levels: taxonomy, concept, unit, then a list of observations. **The same period
appears many times in that list**, once for each filing that reported it. Microsoft's
FY2020 revenue appears in the FY2020 10-K, again in the FY2021 10-K as a comparative, and
again in the FY2022 10-K. Each carries the accession and the date of the filing that said
it, and they do not always agree.

That repetition is not noise to be deduplicated away. It is the point-in-time record, and
:mod:`aer.sources.sec.pit` is what turns it into an answer. This module's only job is to
parse it faithfully and completely — including the observations that will later be
rejected, because a rejection nobody can see is indistinguishable from a fact nobody
found.

**Values are parsed as :class:`~decimal.Decimal` from the raw JSON text.** ``json.loads``
would turn ``143015000000`` into a float, and a float cannot represent every integer above
2^53 exactly. Revenue figures in raw dollars pass that threshold routinely, so a float
round-trip introduces errors in the last digits of numbers that are supposed to be exact.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Final

from aer.core.concepts import canonical_concept
from aer.core.schemas.facts import RawFact
from aer.errors import ExternalServiceError
from aer.sources.sec.tickers import format_cik

__all__ = ["CompanyFacts", "UnmappedConcept", "parse_company_facts"]

# Taxonomies worth parsing. A filer's own extension namespace ("msft", "aapl") holds
# concepts defined by that filer alone, which are meaningless to compare across companies
# and are the one place a fabricated-looking tag is entirely legitimate. They are counted
# and reported, not parsed into facts.
_SHARED_TAXONOMIES: Final[frozenset[str]] = frozenset({"us-gaap", "dei", "ifrs-full", "srt"})


@dataclass(frozen=True, slots=True)
class UnmappedConcept:
    """A tag that produced facts but has no canonical concept.

    Surfaced rather than dropped. A filer tagging segment revenue with a custom element is
    reporting something real, and a concept map with a gap in it should show the gap.
    """

    taxonomy: str
    tag: str
    label: str
    units: tuple[str, ...]
    observations: int


@dataclass(frozen=True, slots=True)
class CompanyFacts:
    """The parsed contents of one companyfacts response."""

    cik: str
    entity_name: str
    facts: tuple[RawFact, ...]
    unmapped: tuple[UnmappedConcept, ...]

    # Concepts skipped entirely because they belong to a filer's own extension namespace.
    # Counted so "why is there no segment revenue?" has an answer.
    extension_concepts: tuple[str, ...] = ()

    def for_concept(self, concept: str) -> tuple[RawFact, ...]:
        """Every observation of one canonical concept, across all filings and periods."""
        return tuple(fact for fact in self.facts if fact.concept == concept)

    @property
    def canonical_facts(self) -> tuple[RawFact, ...]:
        """Only the facts whose tag mapped onto a canonical concept."""
        return tuple(fact for fact in self.facts if fact.is_canonical)

    @property
    def concepts(self) -> frozenset[str]:
        return frozenset(fact.concept for fact in self.facts)


def parse_company_facts(payload: bytes, *, include_unmapped: bool = True) -> CompanyFacts:
    """Parse a companyfacts response.

    Args:
        payload: The raw response body.
        include_unmapped: Whether facts whose tag has no canonical concept are returned as
            facts as well as being listed in ``unmapped``. Default true: dropping them
            would silently lose data whenever the alias map falls behind the taxonomy.

    Raises:
        ExternalServiceError: If the payload is not JSON or is not a companyfacts document.
    """
    document = _load(payload)

    cik_value = document.get("cik")
    if cik_value is None or "facts" not in document:
        message = (
            "The companyfacts response has no cik or no facts block. That is an error "
            "page or a different endpoint, not a facts document."
        )
        raise ExternalServiceError(
            message, provider="sec_edgar", retryable=False, context={"keys": sorted(document)[:10]}
        )

    facts: list[RawFact] = []
    unmapped: list[UnmappedConcept] = []
    extensions: list[str] = []

    taxonomies = document.get("facts") or {}
    if not isinstance(taxonomies, dict):
        message = "The companyfacts facts block is not an object."
        raise ExternalServiceError(message, provider="sec_edgar", retryable=False)

    for taxonomy, concepts in taxonomies.items():
        if not isinstance(concepts, dict):
            continue
        if taxonomy not in _SHARED_TAXONOMIES:
            extensions.extend(f"{taxonomy}:{tag}" for tag in concepts)
            continue

        for tag, definition in concepts.items():
            if not isinstance(definition, dict):
                continue
            parsed, observation_count, units = _parse_concept(taxonomy, tag, definition)

            if canonical_concept(taxonomy, tag) is None:
                unmapped.append(
                    UnmappedConcept(
                        taxonomy=taxonomy,
                        tag=tag,
                        label=str(definition.get("label") or "").strip(),
                        units=units,
                        observations=observation_count,
                    )
                )
                if not include_unmapped:
                    continue
            facts.extend(parsed)

    return CompanyFacts(
        cik=format_cik(cik_value),
        entity_name=str(document.get("entityName", "")).strip(),
        facts=tuple(facts),
        unmapped=tuple(sorted(unmapped, key=lambda u: (u.taxonomy, u.tag))),
        extension_concepts=tuple(sorted(extensions)),
    )


def _load(payload: bytes) -> dict[str, Any]:
    try:
        # parse_float keeps every JSON number out of binary floating point. Values here
        # routinely exceed 2^53, where a float can no longer represent consecutive
        # integers, and a revenue figure that is wrong in its last three digits is worse
        # than one that is obviously missing.
        document = json.loads(payload, parse_float=Decimal, parse_int=Decimal)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        message = f"The SEC companyfacts response is not valid JSON ({exc})."
        raise ExternalServiceError(
            message, provider="sec_edgar", retryable=True, context={"bytes": len(payload)}
        ) from exc

    if not isinstance(document, dict):
        message = "The SEC companyfacts response is not a JSON object."
        raise ExternalServiceError(
            message,
            provider="sec_edgar",
            retryable=False,
            context={"type": type(document).__name__},
        )
    return document


def _parse_concept(
    taxonomy: str, tag: str, definition: dict[str, Any]
) -> tuple[list[RawFact], int, tuple[str, ...]]:
    """Parse every unit and every observation of one concept."""
    canonical = canonical_concept(taxonomy, tag)
    concept = canonical or tag

    units_block = definition.get("units")
    if not isinstance(units_block, dict):
        return [], 0, ()

    facts: list[RawFact] = []
    observations = 0

    for unit, entries in units_block.items():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            observations += 1
            if not isinstance(entry, dict):
                continue
            fact = _parse_observation(
                entry, concept=concept, raw_concept=tag, taxonomy=taxonomy, unit=str(unit)
            )
            if fact is not None:
                facts.append(fact)

    return facts, observations, tuple(sorted(str(u) for u in units_block))


def _parse_observation(
    entry: dict[str, Any], *, concept: str, raw_concept: str, taxonomy: str, unit: str
) -> RawFact | None:
    """One observation, or ``None`` if it lacks what a fact needs to be usable.

    An observation without an end date, a value, an accession or a filed date is not a
    fact this platform can do anything with: it cannot be placed in time, cited, or
    point-in-time filtered. Skipped rather than defaulted — a made-up date is far more
    dangerous than a missing row.
    """
    period_end = _parse_date(entry.get("end"))
    filed_date = _parse_date(entry.get("filed"))
    value = _parse_decimal(entry.get("val"))
    accession = str(entry.get("accn") or "").strip()
    form = str(entry.get("form") or "").strip()

    if period_end is None or filed_date is None or value is None or not accession or not form:
        return None

    try:
        return RawFact(
            concept=concept,
            raw_concept=raw_concept,
            taxonomy=taxonomy,
            unit=unit,
            value=value,
            period_start=_parse_date(entry.get("start")),
            period_end=period_end,
            fiscal_year=_parse_int(entry.get("fy")),
            fiscal_period=str(entry.get("fp")).strip() or None if entry.get("fp") else None,
            form=form,
            accession=accession,
            filed_date=filed_date,
            frame=str(entry.get("frame")).strip() or None if entry.get("frame") else None,
        )
    except ValueError:
        # A malformed accession or a backwards period. One unusable row, not a reason to
        # abandon a document with tens of thousands of good ones.
        return None


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError:
        return None


def _parse_decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _parse_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(Decimal(str(value)))
    except (InvalidOperation, ValueError):
        return None
