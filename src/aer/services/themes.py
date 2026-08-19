"""The theme set a person agreed to, and the rows built from it.

`docs/knowledge-graph.md` K1, ADR 0065. A theme nobody confirmed is a connection nobody
can defend: filing a company under "AI capex" shapes how every later reader weighs it, and
it does so invisibly — which is exactly the shape of judgement this platform routes
through a gate. So the slate goes through ``THEME_SET``, and nothing here writes an edge
without an approval.

The comps pattern, deliberately. ``THEME_STEP`` is the one name both halves use;
:func:`theme_set_payload` is exactly what the gate hashes; :func:`confirmed_theme_set`
refuses rather than returning empty when a proposed slate is undecided or the approval
covers a different one. The one genuinely new piece is :func:`record_confirmed_themes`:
peers live entirely in step output and approvals, but a theme is *shared identity across
runs* — "one key, however many runs name it" — so confirmation has to land in rows a later
run can match its proposals against.

**Keys are slugged in code.** The model's key is a claim about identity; :func:`slugged`
is the identity. However a proposal spells "AI Capex", it matches or founds exactly one
``themes`` row.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping, Sequence
from typing import Any, Final

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.core.enums import Decision, GateKind
from aer.core.hashing import canonical_json, sha256_hex
from aer.db.models import Approval, Job, JobStep, Report, Theme, ThemeMembership
from aer.errors import AerError

__all__ = [
    "THEME_STEP",
    "ThemeSetNotConfirmedError",
    "confirmed_theme_set",
    "existing_vocabulary",
    "gate_payload_for_job",
    "normalised_slate",
    "record_confirmed_themes",
    "slugged",
    "theme_set_payload",
    "theme_set_required",
]

_log = structlog.get_logger("aer.services.themes")

THEME_STEP: Final = "propose_themes"
"""The workflow step whose output carries the slate. One name, used by both halves."""

# How many existing themes the proposer is shown as vocabulary. Newest first, bounded,
# because the list goes into a prompt: a library that has accumulated hundreds of themes
# should offer its current working set, not its whole history.
VOCABULARY_LIMIT: Final = 40

_SLUG = re.compile(r"[^a-z0-9]+")


class ThemeSetNotConfirmedError(AerError):
    """Theme edges were asked for and nobody has agreed to the slate.

    Its own class because the fix is a human action rather than a corrected argument, and
    the message names the gate so an operator knows which one.
    """

    code = "theme_set_not_confirmed"
    http_status = 409


def slugged(key: str) -> str:
    """The one identity a spelling collapses to: lowercase, hyphenated, trimmed.

    Applied before any row is matched or founded, so the model's casing and punctuation
    cannot mint near-duplicates. An empty result — a key that was all punctuation — stays
    empty, and the caller drops the proposal as unusable.
    """
    return _SLUG.sub("-", key.lower()).strip("-")[:64]


def theme_set_payload(produced: Mapping[str, Any]) -> dict[str, Any]:
    """Exactly what the theme-set gate approves, as one structure.

    Built from the proposal step's own output, so the themes an operator confirms are the
    themes that were proposed rather than a re-derivation that might differ. Hashed into
    the approval, so confirming one slate and recording another is refused.
    """
    themes = produced.get("themes", [])
    return {
        "subject": str(produced.get("subject", "")),
        "themes": [
            {
                "key": str(theme.get("key", "")),
                "label": str(theme.get("label", "")),
                "rationale": str(theme.get("rationale", "")),
                # Whether the key matched an existing themes row at proposal time. In the
                # payload because a reviewer weighs "joins a tracked theme" differently
                # from "founds a new one", so it is part of what is being approved.
                "existing": bool(theme.get("existing", False)),
            }
            for theme in themes
            if isinstance(theme, Mapping)
        ],
        "proposed_by": str(produced.get("proposed_by", "")),
    }


def theme_set_required(produced: Mapping[str, Any]) -> bool:
    """Whether this run needs a person to agree its themes.

    On a non-empty slate. A run that proposed none — or whose model call failed — has no
    edges to defend and should not wait at a gate to confirm an empty list; it simply
    contributes no themes, and that is a fact rather than a failure.
    """
    return bool(theme_set_payload(produced)["themes"])


async def normalised_slate(
    session: AsyncSession, entries: Sequence[tuple[str, str, str]]
) -> list[dict[str, Any]]:
    """``(key, label, rationale)`` in; gate-ready slate rows out.

    The identity decisions live here, where a test can reach them with a messy key: each
    key is slugged to its one identity, a key that slugs to nothing is dropped as
    unusable, a repeat of a key already on the slate is dropped rather than shown twice,
    and each survivor is marked ``existing`` by an exact match against the ``themes``
    table — the distinction a reviewer weighs, so it is part of what the gate hashes.
    """
    slate: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_key, label, rationale in entries:
        key = slugged(raw_key)
        if not key or key in seen:
            continue
        seen.add(key)
        exists = await session.scalar(select(Theme.id).where(Theme.key == key)) is not None
        slate.append(
            {
                "key": key,
                "label": label.strip(),
                "rationale": rationale.strip(),
                "existing": exists,
            }
        )
    return slate


async def existing_vocabulary(session: AsyncSession, *, limit: int = VOCABULARY_LIMIT) -> list[str]:
    """The themes the library already tracks, as "key — label" lines for the proposer."""
    rows = await session.scalars(
        select(Theme).order_by(Theme.created_at.desc(), Theme.key).limit(limit)
    )
    return [f"{row.key} — {row.label}" for row in rows]


async def _proposal_step(session: AsyncSession, job_id: uuid.UUID) -> JobStep | None:
    step: JobStep | None = await session.scalar(
        select(JobStep)
        .where(JobStep.job_id == job_id, JobStep.step_key == THEME_STEP)
        .order_by(JobStep.sequence.desc())
        .limit(1)
    )
    return step


async def confirmed_theme_set(session: AsyncSession, job: Job) -> tuple[dict[str, str], ...]:
    """The themes this run may record, and nothing else.

    Returns the confirmed slate as ``{key, label, rationale}`` rows (keys already
    slugged at proposal time).

    Raises:
        ThemeSetNotConfirmedError: If themes were proposed and the ``THEME_SET`` gate has
            not approved them, or the approval was recorded against a different slate.
            Refusals rather than empty results, because "no themes" and "themes nobody
            has agreed to" mean opposite things.
    """
    step = await _proposal_step(session, job.id)
    produced = (step.output_ref or {}) if step is not None else {}
    payload = theme_set_payload(produced)

    if not payload["themes"]:
        return ()

    approval = await session.scalar(
        select(Approval)
        .where(
            Approval.job_id == job.id,
            Approval.gate == GateKind.THEME_SET,
            Approval.decision == Decision.APPROVED,
        )
        .order_by(Approval.decided_at.desc())
        .limit(1)
    )
    if approval is None:
        message = (
            f"This run proposed {len(payload['themes'])} theme(s) and nobody has confirmed "
            "them. A theme shapes how every later reader weighs the company, so it is not "
            "applied on a model's say-so. Decide at the THEME_SET gate."
        )
        raise ThemeSetNotConfirmedError(
            message, context={"job_id": str(job.id), "gate": GateKind.THEME_SET.value}
        )

    expected = sha256_hex(canonical_json(payload))
    if approval.payload_hash and approval.payload_hash != expected:
        message = (
            "The confirmed theme set for this run does not match the one on record. "
            "Somebody approved a different slate, so the approval is not an approval of "
            "this one."
        )
        raise ThemeSetNotConfirmedError(
            message,
            context={
                "job_id": str(job.id),
                "approved": approval.payload_hash,
                "current": expected,
            },
        )

    return tuple(
        {"key": theme["key"], "label": theme["label"], "rationale": theme["rationale"]}
        for theme in payload["themes"]
    )


async def gate_payload_for_job(session: AsyncSession, job_id: uuid.UUID) -> dict[str, Any]:
    """What the theme review page shows, read back from the proposal step.

    An empty payload for a run that has not proposed yet, so a page can render "nothing
    to review" rather than an error.
    """
    step = await _proposal_step(session, job_id)
    if step is None or not step.output_ref:
        return {}
    return theme_set_payload(step.output_ref)


async def record_confirmed_themes(
    session: AsyncSession, *, job: Job, report: Report
) -> tuple[str, ...]:
    """Land the confirmed slate in rows a later run can match against. Idempotent.

    Called where the run's report row is created. The membership points at that report, so
    the edge stays inert until the report is ``immutable`` — the graph and the vault read
    memberships only through approved reports, exactly as peer edges are read.

    A theme row is founded by the first confirmed proposal of its key and *kept* by every
    later one: an existing theme's label is not overwritten, because renaming a shared
    identity from one run's spelling would move every note that projects it.

    Returns the keys recorded, for the step's output. A run with no confirmed themes —
    none proposed, or the gate not passed because nothing required it — records nothing.
    """
    if report.company_id is None:
        return ()
    confirmed = await confirmed_theme_set(session, job)
    recorded: list[str] = []
    for theme in confirmed:
        row = await session.scalar(select(Theme).where(Theme.key == theme["key"]))
        if row is None:
            row = Theme(key=theme["key"], label=theme["label"])
            session.add(row)
            await session.flush()
        membership = await session.scalar(
            select(ThemeMembership).where(
                ThemeMembership.theme_id == row.id,
                ThemeMembership.company_id == report.company_id,
                ThemeMembership.report_id == report.id,
            )
        )
        if membership is None:
            session.add(
                ThemeMembership(
                    theme_id=row.id,
                    company_id=report.company_id,
                    report_id=report.id,
                    rationale=theme["rationale"],
                )
            )
        recorded.append(theme["key"])
    if recorded:
        await session.flush()
        _log.info(
            "themes.recorded",
            job_id=str(job.id),
            report_id=str(report.id),
            keys=recorded,
        )
    return tuple(recorded)
