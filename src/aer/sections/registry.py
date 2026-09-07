"""Which sections a report is made of, resolved from the database.

**Queried, never listed.** ``resolve_sections`` reads ``section_definitions``, filters by
applicability, and returns them in position order. Adding a section — built-in or
user-authored — is an INSERT, and nothing in this module changes.

**The latest version of each key wins.** Definitions are versioned and never edited, so a
key can have several rows; a run pins the highest version at the moment it starts and the
``report_sections`` rows carry that pin. A definition published later cannot retroactively
change a report that has already been rendered.

**Applicability is a predicate over the request, evaluated in code.** ``{"exchange": ["LSE"]}``
means "only for London listings". Empty means always. Evaluated here rather than by a model,
because "does this section apply to a bank?" is a lookup, not a judgement — and a model
that got it wrong would silently omit an analysis nobody noticed was missing.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Final

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from aer.db.models import JobStep, ReportSection, SectionDefinition, SectionStatus
from aer.db.models.request import ResearchRequest

__all__ = [
    "applies_to",
    "create_report_sections",
    "resolve_sections",
    "section_outcomes",
    "sections_for_job",
]

# The step whose output records what happened to each section.
_DRAFT_STEP: Final = "draft"

_log = structlog.get_logger("aer.sections.registry")


async def section_outcomes(
    session: AsyncSession, *, job_id: uuid.UUID
) -> dict[str, dict[str, Any]]:
    """What the draft step recorded about each section, keyed by section.

    Read from the step's own frozen output rather than recomputed: ``SectionExecution``
    already carries the evidence tally, the attempt count, the refusal causes and the
    problems in the producers' own words, and a second derivation would be a place for
    the readers to disagree. Two of them read it — the review page's per-section table,
    and the §2.4 missing-section trigger, which used to report a status where the record
    held a reason.

    Empty for a run that has not drafted, which every caller treats as "nothing to say"
    rather than as an error.
    """
    step = await session.scalar(
        select(JobStep)
        .where(JobStep.job_id == job_id, JobStep.step_key == _DRAFT_STEP)
        .order_by(JobStep.sequence.desc())
        .limit(1)
    )
    produced = (step.output_ref or {}) if step is not None else {}
    rows: dict[str, dict[str, Any]] = {}
    for outcome in [*produced.get("builtin_sections", []), *produced.get("custom_sections", [])]:
        key = str(outcome.get("section_key", ""))
        if key:
            rows[key] = outcome
    return rows


@dataclass(frozen=True, slots=True)
class SectionPlan:
    """A section that will be produced, and why it was included."""

    definition: SectionDefinition
    applicable: bool
    reason: str = ""


async def resolve_sections(
    session: AsyncSession, *, request: ResearchRequest
) -> list[SectionDefinition]:
    """Every section that applies to this request, in position order.

    Returns the highest version of each key. A key with several versions is one that has
    been revised; a run uses the newest at the moment it starts, and pins it.
    """
    rows = await session.scalars(
        select(SectionDefinition).order_by(
            SectionDefinition.position, SectionDefinition.key, SectionDefinition.version
        )
    )

    # Highest version per key. Iterating in ascending version order and overwriting is
    # simpler than a window function and produces the same answer on the handful of rows
    # this table will ever hold.
    latest: dict[str, SectionDefinition] = {}
    for row in rows:
        latest[row.key] = row

    # The depth control controls through the data, not through code (gap O5): the
    # deep-dive sections' rows declare ``{"analysis_mode": ["standard", "full"]}`` in
    # their applicability (migration 0035), so a quick run resolves the core spine and
    # nothing here names a section key. The budgets scale separately in the drafting
    # policy.
    applicable = [row for row in latest.values() if applies_to(row, request)]
    applicable.sort(key=lambda row: (row.position, row.key))

    _log.debug(
        "sections.resolved",
        request_id=str(request.id),
        resolved=[row.key for row in applicable],
        excluded=[row.key for row in latest.values() if row not in applicable],
    )
    return applicable


def applies_to(definition: SectionDefinition, request: ResearchRequest) -> bool:
    """Whether a section applies to a request.

    The predicate language is deliberately tiny: a mapping from request attribute to a
    list of permitted values, all of which must match. Anything richer would be a query
    language embedded in a JSONB column, and the first thing anyone would want is a
    negation, and the second an ``or``, and by then it is a language nobody can test.

    An unknown attribute name makes the section **not** apply, rather than applying by
    default. A predicate nobody can evaluate is a predicate whose author expected it to do
    something, and silently ignoring it would include a section they meant to exclude.
    """
    predicates: dict[str, Any] = definition.applicability or {}
    if not predicates:
        return True

    for attribute, permitted in predicates.items():
        actual = getattr(request, attribute, _MISSING)
        if actual is _MISSING:
            _log.warning(
                "sections.unknown_applicability_attribute",
                section=definition.key,
                attribute=attribute,
            )
            return False

        allowed = permitted if isinstance(permitted, list) else [permitted]
        if _as_value(actual) not in {_as_value(value) for value in allowed}:
            return False

    return True


class _Missing:
    """Sentinel distinguishing "attribute absent" from "attribute is None"."""


_MISSING = _Missing()


def _as_value(value: Any) -> Any:
    """Compare enums by their value, so a predicate can be written as plain JSON."""
    return getattr(value, "value", value)


async def create_report_sections(
    session: AsyncSession,
    *,
    job_id: Any,
    definitions: list[SectionDefinition],
) -> list[ReportSection]:
    """Create the pending ``report_sections`` rows for a run.

    Position is **copied** from the definition rather than joined at render time. The order
    a report was rendered in is a property of that report; a definition whose position
    changes later must not silently reorder something already approved.
    """
    existing = {
        row.section_key
        for row in await session.scalars(
            select(ReportSection).where(ReportSection.job_id == job_id)
        )
    }

    created: list[ReportSection] = []
    for definition in definitions:
        if definition.key in existing:
            continue
        row = ReportSection(
            job_id=job_id,
            section_definition_id=definition.id,
            section_key=definition.key,
            position=definition.position,
            status=SectionStatus.PENDING,
        )
        session.add(row)
        created.append(row)

    if created:
        await session.flush()
        _log.info(
            "sections.created",
            job_id=str(job_id),
            keys=[row.section_key for row in created],
        )
    return created


async def sections_for_job(session: AsyncSession, job_id: Any) -> list[ReportSection]:
    """A run's sections in position order, each with its definition already loaded.

    **The only order a report is ever assembled in**, and the reason a new section needs no
    code change: the workflow, the renderer and the Markdown exporter all call this.

    The definition is eager-loaded rather than left lazy. A caller reading
    ``section.definition`` outside a greenlet — which is every synchronous line of a step
    that has already awaited its query — would otherwise raise ``MissingGreenlet``, and it
    would do so only when the object happened not to be in the identity map already.
    """
    rows = await session.scalars(
        select(ReportSection)
        .where(ReportSection.job_id == job_id)
        .options(selectinload(ReportSection.definition))
        .order_by(ReportSection.position, ReportSection.section_key)
    )
    return list(rows)
