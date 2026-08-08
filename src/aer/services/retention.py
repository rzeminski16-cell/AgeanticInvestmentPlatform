"""Deleting what a licence says must be deleted, and nothing else.

The one module in this codebase that erases evidence, and it is deliberately hard to reach:
it asks for :class:`~aer.storage.retention.PurgeableStore` rather than the ordinary
:class:`~aer.storage.protocol.ArtefactStore`, so a caller wired with the normal store cannot
call into this path at all.

**Three refusals, and each is the whole point.**

1. A payload whose provider's retention class is ``PERMANENT`` is **never** purged. Public
   filings and official statistics have no deletion obligation and invariant 1 has the
   opposite one; a sweep that erased a 10-K would be destroying the thing this platform
   exists to preserve.
2. A purge with no stated reason is refused. "Licence" is not a reason; "the EODHD
   subscription ended on 2027-03-01 and the agreement requires deletion within a month" is,
   and it is what somebody reads two years later when a citation will not resolve.
3. Purging twice is refused. The second call is either a mistake or a second story about the
   same event, and ``artefact_purges.artefact_id`` is unique so the database says so too.

**What is lost, stated rather than hidden.** After a purge, a citation into that artefact can
be shown to *have been* verified — on a date, against a hash, by a recorded method — and can
never be re-verified, because the bytes are gone. That is a real reduction in what this
platform promises, and it is the price of using a licensed source at all. ADR 0031.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

import structlog
from sqlalchemy import func, select, union
from sqlalchemy.ext.asyncio import AsyncSession

from aer.core.enums import Provider
from aer.db.models import (
    AgentRun,
    Artefact,
    ArtefactPurge,
    AuditEvent,
    Report,
    SourceDocument,
    User,
)
from aer.errors import AerError, ValidationError
from aer.fetch.policy import DEFAULT_POLICIES, RetentionClass
from aer.storage.protocol import ArtefactStore
from aer.storage.retention import PurgeableStore

__all__ = [
    "GarbageCollected",
    "IntegrityReport",
    "PermanentArtefactError",
    "PurgeOutcome",
    "collect_garbage",
    "licensed_providers",
    "purge_artefact",
    "purge_provider",
    "purgeable_artefacts",
    "unreferenced_artefacts",
    "verify_store",
]

_log = structlog.get_logger("aer.services.retention")


class PermanentArtefactError(ValidationError):
    """A purge was attempted on evidence no licence asks to be deleted.

    Its own class rather than a bare `ValidationError`, because this is the refusal that
    protects the archive from its own retention machinery, and it should be greppable.
    """

    code = "artefact_retention_is_permanent"


@dataclass(frozen=True, slots=True)
class PurgeOutcome:
    """What a sweep did, in terms somebody can put in a compliance reply."""

    provider: Provider
    purged: int
    bytes_freed: int
    already_purged: int

    @property
    def is_complete(self) -> bool:
        return True


def licensed_providers() -> tuple[Provider, ...]:
    """Every provider whose licence obliges deletion at some point.

    Read from the fetch policies rather than listed here, so adding a paid feed cannot
    accidentally create data nobody knows has an expiry date.
    """
    return tuple(
        provider
        for provider, policy in sorted(DEFAULT_POLICIES.items())
        if policy.retention is RetentionClass.LICENSED
    )


async def purgeable_artefacts(session: AsyncSession, *, provider: Provider) -> Sequence[Artefact]:
    """Artefacts acquired from a licensed provider whose payloads are still present.

    Joined through ``source_documents``, because the *provider* is a property of the
    acquisition rather than of the bytes: the same content could in principle arrive from two
    places, and it is the licence it arrived under that obliges the deletion.

    Raises:
        PermanentArtefactError: If the provider has no deletion obligation. Asking for the
            purgeable artefacts of the SEC is a question with a dangerous answer if it were
            ever anything but a refusal.
    """
    _require_licensed(provider)

    rows = await session.scalars(
        select(Artefact)
        .join(SourceDocument, SourceDocument.artefact_id == Artefact.id)
        .outerjoin(ArtefactPurge, ArtefactPurge.artefact_id == Artefact.id)
        .where(SourceDocument.provider == provider, ArtefactPurge.id.is_(None))
        .order_by(Artefact.created_at)
        .distinct()
    )
    return list(rows)


async def purge_artefact(
    session: AsyncSession,
    store: PurgeableStore,
    *,
    artefact: Artefact,
    reason: str,
    actor: User | str,
) -> ArtefactPurge:
    """Erase one payload and append the record of it.

    The store is emptied **before** the row is written, and the row is written whether or not
    any bytes were there to free. An idempotent purge that found nothing still has to be
    recorded: the obligation is that the bytes are absent, and a compliance reply saying "we
    deleted it" needs to be true rather than merely attempted.

    Raises:
        PermanentArtefactError: If no source document for this artefact came from a provider
            with a deletion obligation.
        ValidationError: If the reason is blank, or the artefact has already been purged.
    """
    if not reason.strip():
        message = (
            "A purge with no stated reason destroys evidence and explains nothing. Name the "
            "obligation: which agreement, which date, which clause."
        )
        raise ValidationError(message, context={"artefact_id": str(artefact.id)})

    existing = await session.scalar(
        select(ArtefactPurge).where(ArtefactPurge.artefact_id == artefact.id)
    )
    if existing is not None:
        message = (
            f"This artefact was already purged at {existing.purged_at} because "
            f"{existing.reason!r}. A second purge is either a mistake or a second story "
            "about one event."
        )
        raise ValidationError(message, context={"artefact_id": str(artefact.id)})

    provider, licence_note = await _licensed_provider_for(session, artefact)

    freed = await store.purge(artefact.sha256)

    actor_name = actor.email if isinstance(actor, User) else actor
    actor_id = actor.id if isinstance(actor, User) else None

    purge = ArtefactPurge(
        artefact_id=artefact.id,
        reason=reason,
        licence_note=licence_note,
        actor=actor_name,
        actor_user_id=actor_id,
        bytes_freed=freed,
    )
    session.add(purge)

    previous = await session.scalar(select(AuditEvent).order_by(AuditEvent.id.desc()).limit(1))
    session.add(
        AuditEvent.create_linked(
            actor=actor_name,
            event_type="artefact.purged",
            payload={
                "artefact_id": str(artefact.id),
                "sha256": artefact.sha256,
                "provider": provider.value,
                "reason": reason,
                "bytes_freed": freed,
            },
            previous=previous,
        )
    )
    await session.flush()

    _log.warning(
        "retention.purged",
        artefact_id=str(artefact.id),
        sha256=artefact.sha256,
        provider=provider.value,
        bytes_freed=freed,
        actor=actor_name,
    )
    return purge


async def purge_provider(
    session: AsyncSession,
    store: PurgeableStore,
    *,
    provider: Provider,
    reason: str,
    actor: User | str,
) -> PurgeOutcome:
    """Erase every outstanding payload from one licensed provider.

    The operation a terminated subscription needs: one call, one reason, one audit entry per
    artefact, and a count somebody can quote back to the provider.
    """
    artefacts = await purgeable_artefacts(session, provider=provider)

    freed = 0
    for artefact in artefacts:
        purge = await purge_artefact(session, store, artefact=artefact, reason=reason, actor=actor)
        freed += purge.bytes_freed

    outcome = PurgeOutcome(
        provider=provider,
        purged=len(artefacts),
        bytes_freed=freed,
        already_purged=0,
    )
    _log.warning(
        "retention.provider_purged",
        provider=provider.value,
        purged=outcome.purged,
        bytes_freed=outcome.bytes_freed,
        reason=reason,
    )
    return outcome


# -- Internals -----------------------------------------------------------------------------


def _require_licensed(provider: Provider) -> None:
    policy = DEFAULT_POLICIES.get(provider)
    if policy is not None and policy.retention is RetentionClass.LICENSED:
        return

    message = (
        f"{provider.value} material is retained permanently and is not purgeable. Nothing in "
        "its terms asks for deletion, and invariant 1 asks for the opposite: a filing erased "
        "is a report that can no longer be checked. Only providers whose licence obliges "
        f"deletion may be purged, which is currently "
        f"{', '.join(p.value for p in licensed_providers()) or 'none of them'}."
    )
    raise PermanentArtefactError(message, context={"provider": provider.value})


async def _licensed_provider_for(session: AsyncSession, artefact: Artefact) -> tuple[Provider, str]:
    """The licensed provider this artefact came from, and the terms it arrived under.

    Refuses an artefact with no licensed acquisition, which is the guard that stops a
    mistyped id erasing a filing. The licence note is taken from the source document rather
    than from today's policy: a purge is defensible against the terms in force at the time.
    """
    documents = list(
        await session.scalars(
            select(SourceDocument).where(SourceDocument.artefact_id == artefact.id)
        )
    )
    for document in documents:
        policy = DEFAULT_POLICIES.get(document.provider)
        if policy is not None and policy.retention is RetentionClass.LICENSED:
            return document.provider, document.licence_note or policy.licence_note

    providers = sorted({document.provider.value for document in documents})
    message = (
        f"This artefact was acquired from {', '.join(providers) or 'no recorded provider'}, "
        "none of which has a deletion obligation. Purging it would destroy evidence no "
        "licence asks to be destroyed."
    )
    raise PermanentArtefactError(
        message, context={"artefact_id": str(artefact.id), "providers": ",".join(providers)}
    )


def purge_uuid(value: str) -> uuid.UUID:
    """Parse an artefact id from an operator's input, refusing anything malformed."""
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        message = f"{value!r} is not an artefact id."
        raise ValidationError(message, context={"given": value}) from exc


# ==========================================================================================
# The sweeps: proving the archive is intact, and clearing what nothing points at
# ==========================================================================================
#
# **Retention had a delete path and no caller.** `purge_provider` answers a licence's
# demand to destroy copies, and until there is a licensed provider in the store it answers
# a question nobody is asking. The two below are the ones a single-machine platform needs
# every week: is the evidence still the evidence, and how much of the disk is holding bytes
# nothing refers to.


@dataclass(frozen=True, slots=True)
class GarbageCollected:
    """What a sweep of unreferenced artefacts found, and whether it acted.

    Its own type rather than :class:`PurgeOutcome`: that one is provider-scoped because a
    licensed purge answers one publisher's terms, and this is not about a publisher at all.
    Naming a provider here would put a plausible, wrong word in a compliance answer.
    """

    found: int
    bytes_freed: int
    deleted: bool

    @property
    def reclaimable_bytes(self) -> int:
        """What a real sweep would free. The same figure either way — a dry run reports
        what it would have done, and a caller comparing the two should see one number."""
        return self.bytes_freed


@dataclass(frozen=True, slots=True)
class IntegrityReport:
    """What a sweep of the store found.

    ``skipped`` counts the artefacts whose bytes are *supposed* to be gone. A licensed
    purge leaves the row and removes the payload, so a sweep that did not know about
    :class:`~aer.db.models.ArtefactPurge` would report every honoured licence obligation
    as data loss — and an alert that cries wolf on its own correct behaviour is an alert
    somebody turns off.
    """

    checked: int
    intact: int
    corrupt: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    skipped: int = 0

    @property
    def is_sound(self) -> bool:
        return not self.corrupt and not self.missing


async def verify_store(session: AsyncSession, store: ArtefactStore) -> IntegrityReport:
    """Re-read every artefact and check it still hashes to its name.

    **Invariant 1 is a claim about the present tense.** "Every externally derived fact
    traces to a hashed artefact" is only true while the artefact still matches its hash —
    a disk that silently rotted a byte would leave every citation over that document
    verifying against text nobody filed. The store checks the digest on each read, so a
    corrupt artefact is caught the moment something needs it; this is what catches it
    before that, when there is still a backup to restore from.

    Missing and corrupt are counted apart because they call for different responses: a
    file that is gone is a deletion or a botched move and wants a backup, and one whose
    bytes have changed is the disk and wants a new one. The store cannot make that
    distinction — :meth:`~aer.storage.protocol.ArtefactStore.verify` raises
    ``IntegrityError`` for both, deliberately, because to a reader either answer means "do
    not trust this" — so presence is asked separately here.
    """
    corrupt: list[str] = []
    missing: list[str] = []
    checked = 0

    purged = select(ArtefactPurge.artefact_id)
    statement = select(Artefact.sha256).where(Artefact.id.not_in(purged)).order_by(Artefact.sha256)
    keepable = list(await session.scalars(statement))
    total = await session.scalar(select(func.count()).select_from(Artefact)) or 0

    for sha256 in keepable:
        checked += 1
        if not await store.exists(sha256):
            missing.append(sha256)
            continue
        try:
            await store.verify(sha256)
        except AerError:
            # It is there and it does not hash to its name: the bytes changed after they
            # were archived, which is the failure invariant 1 cannot survive quietly.
            corrupt.append(sha256)

    report = IntegrityReport(
        checked=checked,
        intact=checked - len(corrupt) - len(missing),
        corrupt=tuple(corrupt),
        missing=tuple(missing),
        skipped=total - checked,
    )
    _log.info(
        "retention.verified",
        checked=report.checked,
        intact=report.intact,
        corrupt=len(report.corrupt),
        missing=len(report.missing),
        skipped=report.skipped,
    )
    return report


async def unreferenced_artefacts(session: AsyncSession) -> Sequence[Artefact]:
    """Artefacts nothing in the database points at.

    Every reference to an artefact is ``RESTRICT``, which is what stops evidence being
    deleted out from under the report that cites it — so an artefact with no referrer is
    one no citation, no report and no agent run can reach. Deleting it takes nothing away
    from invariant 1: there is no fact tracing to it.

    They accumulate honestly rather than through a bug. `aer reset-research` clears the
    runs and leaves the content-addressed bytes, which is the right order to do it in —
    the alternative is deleting artefacts a surviving run still needs.
    """
    # **Every branch filters its nulls, and the sweep is worthless without it.** Four of
    # these columns are optional, so one agent run with no archived request payload puts a
    # NULL in the set — and `x NOT IN (…, NULL)` is NULL, never true, for every row. The
    # query would return no orphans at all and look exactly like a clean store.
    referenced = union(
        select(SourceDocument.artefact_id.label("artefact_id")).where(
            SourceDocument.artefact_id.is_not(None)
        ),
        select(ArtefactPurge.artefact_id.label("artefact_id")).where(
            ArtefactPurge.artefact_id.is_not(None)
        ),
        select(AgentRun.request_payload_ref.label("artefact_id")).where(
            AgentRun.request_payload_ref.is_not(None)
        ),
        select(AgentRun.response_payload_ref.label("artefact_id")).where(
            AgentRun.response_payload_ref.is_not(None)
        ),
        select(Report.pdf_artefact_id.label("artefact_id")).where(
            Report.pdf_artefact_id.is_not(None)
        ),
        select(Report.markdown_artefact_id.label("artefact_id")).where(
            Report.markdown_artefact_id.is_not(None)
        ),
        select(Report.html_artefact_id.label("artefact_id")).where(
            Report.html_artefact_id.is_not(None)
        ),
    ).subquery()
    statement = (
        select(Artefact)
        .where(Artefact.id.not_in(select(referenced.c.artefact_id)))
        .order_by(Artefact.created_at)
    )
    return list(await session.scalars(statement))


async def collect_garbage(
    session: AsyncSession, store: PurgeableStore, *, dry_run: bool = True
) -> GarbageCollected:
    """Delete the bytes and the rows for artefacts nothing points at.

    Dry by default. A sweep that deletes on its first invocation is a sweep somebody runs
    once by accident, and the count it would have deleted is the thing worth seeing first.

    Not :func:`purge_artefact`: that path is for a *licence* demanding deletion of evidence
    a run still cites, so it keeps the row and the provenance and refuses anything
    permanent. Here there is no provenance to keep — nothing refers to these — and the row
    goes with the bytes.
    """
    orphans = await unreferenced_artefacts(session)
    freed = sum(artefact.size_bytes for artefact in orphans)

    if not dry_run:
        for artefact in orphans:
            await store.purge(artefact.sha256)
            await session.delete(artefact)
        await session.flush()

    _log.info(
        "retention.collected",
        artefacts=len(orphans),
        bytes_freed=freed,
        dry_run=dry_run,
    )
    return GarbageCollected(found=len(orphans), bytes_freed=freed, deleted=not dry_run)
