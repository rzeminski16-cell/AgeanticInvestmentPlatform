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
    "CompanyNoteMeta",
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
