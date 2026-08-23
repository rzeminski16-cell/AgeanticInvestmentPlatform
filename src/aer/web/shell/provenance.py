"""Where a figure came from, and whether anybody has agreed to it.

Two questions, two chips. The product specification asked for one control carrying five
values — Suggested, Approved, User entered, Calculated, Source fact — and ADR 0077 refused
it, because those are two axes wearing one hat. *Where a number came from* and *whether a
person has confirmed it* vary independently: "a calculation nobody has confirmed" is the
ordinary state of half an assumptions page, and a single chip has no way to say it.

The second collapse is worse. "Approved" would cover both `Assumption.approved` — a person
agreed to this value — and an `approvals` row carrying a `payload_hash` — a person agreed to
*exactly this page*. The second is far the stronger guarantee, and spending one word on both
overstates what the platform knows about the weaker one.

**A badge is always a link.** `ProvenanceRef` requires an `href`, so a badge cannot be
rendered without somewhere to read the lineage. One that said "Calculated" and led nowhere
would assert a chain while refusing to show it, which is the confidently-wrong surface this
platform exists to prevent.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Final

from aer.core.dates import format_date

__all__ = ["Confirmation", "Provenance", "ProvenanceRef", "confirmed_by", "suggested"]

# Through `format_date` rather than `strftime`, because the no-padding day directive this
# pattern uses is a glibc extension that raises on Windows. That module expands it to
# digits before the C library sees the pattern; a badge is chrome, but a chrome that
# crashes the page it decorates is not better than one with a padded day.
_CONFIRMED_ON: Final = "%-d %B %Y"


class Provenance(StrEnum):
    """Which class of record a figure is, in the taxonomy the ADRs settled.

    The values are the record kinds themselves, not a presentation vocabulary, so a badge
    cannot describe a figure as something the schema has no room for.
    """

    SOURCE_FACT = "Source fact"
    """Somebody else published it, on a date, and the bytes are archived."""

    CALCULATED = "Calculated"
    """A recorded calculation: a formula, its inputs, and the code version (ADR 0011)."""

    ATTESTED = "Attested"
    """What the operator's own book says, at a grade of evidence (ADR 0073)."""

    ASSUMED = "Assumed"
    """A value chosen rather than observed, with its justification (ADR 0046)."""

    JUDGED = "Judged"
    """A view a named person held. Never evidence for anything (ADR 0074)."""


class Confirmation(StrEnum):
    """Whether a person has agreed to it, which is a different question from where it came from."""

    SUGGESTED = "Suggested"
    """Proposed by the platform and awaiting a decision. It has changed nothing yet."""

    UNCONFIRMED = "Unconfirmed"
    """Recorded, and nobody has agreed to it. Not the same as suggested: no one proposed it."""

    CONFIRMED = "Confirmed"
    """A named person agreed, at a time. Rendered with both, because a bare 'confirmed'
    invites the reader to supply their own idea of who and when."""


@dataclass(frozen=True, slots=True)
class ProvenanceRef:
    """A figure's provenance, and where to go and read it.

    ``href`` is required and has no default. The macro takes this object rather than a
    string, so there is no way to render the badge without having decided where it points.
    """

    kind: Provenance
    identifier: str
    href: str
    confirmation: Confirmation = Confirmation.UNCONFIRMED
    confirmed_by_name: str = ""
    confirmed_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.href:
            message = (
                f"A {self.kind.value} badge for {self.identifier or 'an unnamed figure'} has "
                "no href. A provenance badge is a link to its own drill-down; one that "
                "leads nowhere asserts a lineage while refusing to show it."
            )
            raise ValueError(message)
        if self.confirmation is Confirmation.CONFIRMED and not self.confirmed_by_name:
            message = (
                "A confirmed badge must name who confirmed it. 'Confirmed' with nobody "
                "attached is the approval equivalent of an uncited figure."
            )
            raise ValueError(message)

    @property
    def confirmation_text(self) -> str:
        """The confirmation chip's words, including who and when where there are any."""
        if self.confirmation is not Confirmation.CONFIRMED:
            return self.confirmation.value
        when = f" on {format_date(self.confirmed_at, _CONFIRMED_ON)}" if self.confirmed_at else ""
        return f"Confirmed by {self.confirmed_by_name}{when}"


def suggested(kind: Provenance, identifier: str, href: str) -> ProvenanceRef:
    """A figure the platform proposed and nobody has decided on."""
    return ProvenanceRef(
        kind=kind, identifier=identifier, href=href, confirmation=Confirmation.SUGGESTED
    )


def confirmed_by(
    kind: Provenance, identifier: str, href: str, *, name: str, at: datetime | None = None
) -> ProvenanceRef:
    """A figure a named person agreed to."""
    return ProvenanceRef(
        kind=kind,
        identifier=identifier,
        href=href,
        confirmation=Confirmation.CONFIRMED,
        confirmed_by_name=name,
        confirmed_at=at,
    )
