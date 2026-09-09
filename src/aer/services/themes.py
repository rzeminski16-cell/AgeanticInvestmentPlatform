"""The theme set a person agreed to, and the rows built from it.

`docs/archive/knowledge-graph.md` K1, ADR 0065. A theme nobody confirmed is a connection nobody
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
from aer.db.models import (
    Approval,
    Job,
    JobStep,
    OperatorTheme,
    Report,
    Theme,
    ThemeMembership,
    User,
)
from aer.errors import AerError, ValidationError

__all__ = [
    "THEME_STEP",
    "ThemeSetNotConfirmedError",
    "add_operator_theme",
    "confirmed_theme_set",
    "existing_vocabulary",
    "gate_payload_for_job",
    "normalised_slate",
    "operator_themes_for_job",
    "payload_for_job",
    "record_confirmed_themes",
    "slate_for_job",
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
    # The whole slate, additions included: the operator approved what the page showed, and
    # the page shows both halves. Verifying against the proposal alone would accept an
    # approval of a slate nobody was ever offered.
    payload = await payload_for_job(session, job.id)

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
    return await payload_for_job(session, job_id)


async def payload_for_job(session: AsyncSession, job_id: uuid.UUID) -> dict[str, Any]:
    """The gate's payload over the whole slate — the step's proposal and the operator's.

    **One funnel.** The page renders this, the approval hashes this, and
    `confirmed_theme_set` verifies against this, so the three cannot disagree about what
    was agreed to. It is the reason an addition is a row rather than a rewrite of the
    step's recorded output: the record stays the record, and the payload is assembled.

    Adding a theme after approving changes the payload and therefore invalidates the
    approval — which is the stale-approval rule working, not a problem to route around.
    """
    step = await _proposal_step(session, job_id)
    produced = (step.output_ref or {}) if step is not None else {}
    payload = theme_set_payload(produced)
    payload["themes"] = await slate_for_job(session, job_id)
    return payload


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


async def operator_themes_for_job(
    session: AsyncSession, job_id: uuid.UUID
) -> list[tuple[str, str, str]]:
    """What the operator added to this run's slate, as ``normalised_slate`` takes it.

    Oldest first, so the slate reads in the order it was built and a second render puts
    nothing in a new place.
    """
    rows = await session.scalars(
        select(OperatorTheme)
        .where(OperatorTheme.job_id == job_id)
        .order_by(OperatorTheme.created_at, OperatorTheme.key)
    )
    return [(row.key, row.label, row.rationale) for row in rows]


async def add_operator_theme(
    session: AsyncSession, *, job: Job, label: str, rationale: str, actor: User
) -> OperatorTheme:
    """Put one theme of the operator's own on this run's slate.

    **An addition, not a confirmation.** The row joins the slate the gate is about to
    hash; the gate's approval is still what files the company under anything. Adding one
    after approving invalidates that approval by changing the payload, which is the stale
    approval rule working rather than a problem to work around.

    The key is slugged by the same function a model's proposal passes through, so an
    operator founding a theme and a model founding one cannot produce two spellings of one
    identity.

    Raises:
        ValidationError: If the label slugs to nothing, if the rationale is blank, or if
            this run's slate already carries the key — from the model or from the
            operator. A rationale is required for the reason the gate shows every one at
            full length: a theme shapes how every later reader weighs the company, and it
            does so invisibly.
    """
    key = slugged(label)
    if not key:
        message = (
            f"{label!r} does not name a theme. A theme's identity is its key, and this "
            "one has no letters or digits to make one from."
        )
        raise ValidationError(message, context={"label": label})
    if not rationale.strip():
        message = (
            "A theme needs a reason. It shapes how every later reader of the library "
            "weighs this company, and it does so invisibly — which is why the gate shows "
            "every rationale at full length."
        )
        raise ValidationError(message, context={"key": key})

    proposed = {theme["key"] for theme in await slate_for_job(session, job.id)}
    if key in proposed:
        message = (
            f"This run's slate already carries {key!r}. A theme joins a run once; if the "
            "rationale on it is wrong, that is the proposal to argue with rather than a "
            "second row."
        )
        raise ValidationError(message, context={"key": key, "job_id": str(job.id)})

    row = OperatorTheme(
        job_id=job.id,
        key=key,
        label=label.strip(),
        rationale=rationale.strip(),
        added_by=actor.email,
    )
    session.add(row)
    await session.flush()

    _log.info(
        "theme.added_by_operator",
        job_id=str(job.id),
        key=key,
        actor=actor.email,
    )
    return row


async def slate_for_job(session: AsyncSession, job_id: uuid.UUID) -> list[dict[str, Any]]:
    """The run's whole slate: what the step proposed, then what the operator added.

    One funnel, so the page, the gate's hash and `confirmed_theme_set` cannot disagree
    about what is being approved. Both halves go through `normalised_slate`, which is
    where slugging, de-duplication and the ``existing`` flag live — an operator's addition
    is held to the identity rules a model's proposal is held to.
    """
    step = await _proposal_step(session, job_id)
    produced = (step.output_ref or {}) if step is not None else {}
    proposed = theme_set_payload(produced)["themes"]
    added = await operator_themes_for_job(session, job_id)
    if not added:
        return list(proposed)
    entries = [(theme["key"], theme["label"], theme["rationale"]) for theme in proposed] + added
    return await normalised_slate(session, entries)
