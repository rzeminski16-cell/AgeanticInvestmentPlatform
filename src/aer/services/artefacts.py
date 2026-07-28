"""Storing evidence: bytes to the store, one row to the database.

The store is content-addressed, so storing the same filing twice already produces one
file. This layer makes the database agree: one row per distinct content, whatever
happened above it.

**There is no update path and no delete path here, deliberately.** An artefact is
identified by the hash of its own content, so an artefact that changes is a different
artefact. The database enforces that with a trigger; this module simply never asks. See
``docs/adr/0008-content-addressed-immutable-artefacts.md``.
"""

from __future__ import annotations

from collections.abc import AsyncIterable
from dataclasses import dataclass

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError as DbIntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from aer.db.models import Artefact, AuditEvent
from aer.errors import IntegrityError
from aer.storage.protocol import ArtefactStore, StoredArtefact

__all__ = ["ArtefactRecord", "store_artefact", "store_artefact_stream", "verify_artefact"]

_log = structlog.get_logger("aer.services.artefacts")

DEFAULT_MEDIA_TYPE = "application/octet-stream"


@dataclass(frozen=True, slots=True)
class ArtefactRecord:
    """A persisted artefact, and whether this call is what created it."""

    artefact: Artefact
    sha256: str
    was_new: bool

    @property
    def id(self) -> object:
        return self.artefact.id


async def store_artefact(
    session: AsyncSession,
    store: ArtefactStore,
    *,
    data: bytes,
    media_type: str = DEFAULT_MEDIA_TYPE,
) -> ArtefactRecord:
    """Store bytes and return their artefact row, creating it only if it is new.

    Raises:
        ValidationError: If the payload exceeds the configured size cap.
        IntegrityError: If the bytes read back do not match what was written.
    """
    stored = await store.put_bytes(data)
    return await _record(session, store, stored, media_type=media_type)


async def store_artefact_stream(
    session: AsyncSession,
    store: ArtefactStore,
    *,
    chunks: AsyncIterable[bytes],
    media_type: str = DEFAULT_MEDIA_TYPE,
) -> ArtefactRecord:
    """As :func:`store_artefact`, for content too large to hold in memory."""
    stored = await store.put_stream(chunks)
    return await _record(session, store, stored, media_type=media_type)


async def _record(
    session: AsyncSession,
    store: ArtefactStore,
    stored: StoredArtefact,
    *,
    media_type: str,
) -> ArtefactRecord:
    existing = await session.scalar(select(Artefact).where(Artefact.sha256 == stored.sha256))
    if existing is not None:
        # Already known. Not an error and not a second copy — the same bytes are the same
        # artefact, however many times they arrive.
        _log.debug("artefact.deduplicated", sha256=stored.sha256, size_bytes=stored.size_bytes)
        return ArtefactRecord(artefact=existing, sha256=stored.sha256, was_new=False)

    artefact = Artefact(
        sha256=stored.sha256,
        media_type=media_type,
        size_bytes=stored.size_bytes,
        storage_backend="local",
        storage_key=store.storage_key_for(stored.sha256),
    )

    try:
        # Added *and* flushed inside the savepoint, not merely flushed. Rolling back to a
        # savepoint restores the session to its state at that point, which expunges
        # objects added after it — so a losing insert leaves nothing pending. Adding
        # before the savepoint instead leaves the object attached, and the next autoflush
        # retries the same doomed INSERT outside any savepoint, taking the whole
        # transaction down with it.
        async with session.begin_nested():
            session.add(artefact)
            await session.flush()
    except DbIntegrityError:
        # Another writer inserted the same digest between the SELECT and the INSERT. The
        # unique constraint is what makes that safe: the loser reads the winner's row, and
        # both callers end up with the same artefact. Handling it here rather than locking
        # keeps the common path free of contention.
        winner = await session.scalar(select(Artefact).where(Artefact.sha256 == stored.sha256))
        if winner is None:  # pragma: no cover -- would mean the constraint fired for another reason
            raise
        _log.debug("artefact.race_lost", sha256=stored.sha256)
        return ArtefactRecord(artefact=winner, sha256=stored.sha256, was_new=False)

    _log.info(
        "artefact.stored",
        sha256=stored.sha256,
        size_bytes=stored.size_bytes,
        media_type=media_type,
    )
    return ArtefactRecord(artefact=artefact, sha256=stored.sha256, was_new=True)


async def verify_artefact(
    session: AsyncSession,
    store: ArtefactStore,
    *,
    sha256: str,
    actor: str = "system",
) -> int:
    """Re-read a stored artefact and confirm it still matches its content address.

    Returns its size in bytes.

    A failure is recorded in the audit log before it is raised. An integrity failure is
    exactly the event a later investigation needs to find, and an exception that only
    reaches a log line is an exception that gets lost when the process restarts.

    Raises:
        IntegrityError: If the artefact is missing or its content has changed.
    """
    try:
        return await store.verify(sha256)
    except IntegrityError as exc:
        previous = await session.scalar(select(AuditEvent).order_by(AuditEvent.id.desc()).limit(1))
        session.add(
            AuditEvent.create_linked(
                actor=actor,
                event_type="artefact.integrity_failed",
                payload={"sha256": sha256, "detail": exc.message, **exc.context},
                previous=previous,
            )
        )
        await session.flush()
        _log.error("artefact.integrity_failed", sha256=sha256, detail=exc.message)
        raise
