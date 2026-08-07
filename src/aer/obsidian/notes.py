"""What a vault note declares about itself: the section 2.8 frontmatter, as models.

Pydantic models rather than dicts, so an export that would write malformed frontmatter
fails in Python before a byte reaches the vault. Serialised through ``python-frontmatter``
with sorted keys, which is half of what makes a second export byte-identical to the
first; the other half is that every timestamp in a note is the *approval's*, never the
export's — the note describes the report, and the report has exactly one honest date.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

import frontmatter
from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "SENTINEL",
    "CatalystNoteMeta",
    "CompanyNoteMeta",
    "IndustryNoteMeta",
    "RunNoteMeta",
    "SourceNoteMeta",
    "render_note",
]

SENTINEL = "<!-- AER:END-GENERATED -->"

AER_SCHEMA = 1


class _NoteMeta(BaseModel):
    """The fields every generated note carries."""

    model_config = ConfigDict(extra="forbid")

    aer_id: str
    aer_kind: str
    aer_schema: int = AER_SCHEMA
    generated_at: datetime
    generator: str
    tags: list[str] = Field(default_factory=list)


class RunNoteMeta(_NoteMeta):
    """The run note's frontmatter — the section 2.8 schema, honestly filled.

    Fields the platform cannot yet source (ISIN, an analysis mode) are absent rather
    than invented; frontmatter that guesses is frontmatter nobody can trust.
    """

    aer_kind: Literal["run"] = "run"
    report_id: str
    job_id: str
    workflow_version: str
    company: str
    ticker: str
    exchange: str
    as_of_date: date
    base_currency: str
    point_in_time: bool
    rating: str | None = None
    confidence: float | None = None
    valuation: dict[str, Any] | None = None
    horizon_months: int | None = None
    aliases: list[str] = Field(default_factory=list)
    company_note: str = ""
    industry_note: str | None = None
    competitors: list[str] = Field(default_factory=list)
    # Named for what it holds — links to catalyst notes — and deliberately not the
    # section 2.8 illustrative key, which collides with a seeded section key that no
    # module may name (the no-hardcoded-section-key scan holds this file to that too).
    catalyst_notes: list[str] = Field(default_factory=list)
    source_notes: list[str] = Field(default_factory=list)
    content_hash: str
    custom_sections: list[dict[str, Any]] = Field(default_factory=list)
    evidence_policy: str = "derived-from-approved-run; claims require re-sourcing before reuse"


class CompanyNoteMeta(_NoteMeta):
    aer_kind: Literal["company"] = "company"
    company: str
    ticker: str
    exchange: str
    run_notes: list[str] = Field(default_factory=list)
    industry_note: str | None = None
    competitors: list[str] = Field(default_factory=list)


class IndustryNoteMeta(_NoteMeta):
    """An industry note names its companies; each company note names it back.

    The ``companies`` list is the back-link half of the section 2.8 company-industry
    convention, and it is recomputed from the database on every regeneration — membership
    is a statement about confirmed classifications, not about which export ran last.
    """

    aer_kind: Literal["industry"] = "industry"
    sector_key: str
    label: str
    companies: list[str] = Field(default_factory=list)


class CatalystNoteMeta(_NoteMeta):
    """One expected event, the runs whose theses lean on it, and — later — its outcome.

    ``thesis_refs`` carries run ``aer_id``s rather than wiki-links, per section 2.8: a
    reference to a thesis is data a query can follow, not a rendering choice.
    ``resolution`` stays absent until an approved run's as-of date lies beyond the
    catalyst's parsed deadline; "passed" is a statement about the calendar and the
    research record, never about whether the event actually happened.
    """

    aer_kind: Literal["catalyst"] = "catalyst"
    company: str
    ticker: str
    label: str
    expected_timing: str
    deadline: date | None = None
    status: str
    thesis_refs: list[str] = Field(default_factory=list)
    resolution: str | None = None


class SourceNoteMeta(_NoteMeta):
    aer_kind: Literal["source"] = "source"
    title: str
    url: str
    publisher: str | None = None
    provider: str
    source_tier: str
    publication_date: date | None = None
    retrieved_at: datetime
    artefact_sha256: str | None = None


def render_note(meta: _NoteMeta, body: str) -> str:
    """One note as text: validated frontmatter above, the body below.

    Keys are sorted and ``None`` fields dropped, so the serialisation is a function of
    the values alone — the property the idempotence test holds.
    """
    post = frontmatter.Post(body, **meta.model_dump(mode="json", exclude_none=True))
    return frontmatter.dumps(post, sort_keys=True) + "\n"
