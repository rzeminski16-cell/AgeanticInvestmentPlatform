"""Append-only, tamper-evident audit log.

Every record links to its predecessor by hash, so altering a past entry invalidates every
entry after it. That does not make the log immutable — anyone with write access to the
database can rewrite rows — but it makes tampering *detectable*, which is the achievable
property and the one that matters when the question is "can I trust this record of what
happened".

Two things are deliberately deferred and must not be forgotten:

* **Database-level append-only enforcement.** Revoking UPDATE and DELETE from the
  application role is what turns "we never update this table" from a convention into a
  guarantee. It needs a dedicated migration role to exist first, so it arrives with the
  deployment work rather than here.
* **Chain verification on startup and in CI.** The verifier exists
  (:func:`aer.core.hashing.verify_chain`); wiring it into a scheduled check comes with the
  hardening phase.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from aer.core.hashing import chain_hash
from aer.db.base import Base, created_at_column
from aer.db.types import BigIntPk, Sha256, Timestamp

__all__ = ["AuditEvent"]


class AuditEvent(Base):
    __tablename__ = "audit_events"

    # A monotonic integer, not a UUID: the primary key *is* the chain order, and that
    # ordering has to be unambiguous when verifying.
    id: Mapped[BigIntPk]

    # Nullable and intentionally not foreign keys. An audit record must survive the thing
    # it describes -- including a deleted request -- or the log would quietly lose exactly
    # the entries most worth keeping.
    job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    request_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))

    # What the event was about, in the vocabulary ADR 0071's registry closes. The existing
    # two columns were built when every consequential record was a research record; a trade
    # entry, a position correction and a thesis edit would each chain against NULL, NULL —
    # present in the ordering, counted by the verifier, and unreachable by any query asking
    # what has happened to a position.
    #
    # These are added *before* the first row that needs them, and that is the whole point.
    # `this_hash` is chain_hash(prev_hash, payload), so the correlation sits outside the
    # digest and two columns can be added without disturbing a single existing chain. What
    # cannot be done later is filling them: an event written before they existed recorded
    # its subject nowhere, so a backfill would be an invention, written by UPDATE against a
    # table whose whole discipline is that rows are appended and never altered.
    subject_kind: Mapped[str | None] = mapped_column(String(32))
    subject_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))

    # Who or what acted: a user id, "system", or a worker identifier.
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    # NULL only for the first record in the chain.
    prev_hash: Mapped[str | None] = mapped_column(Text)
    this_hash: Mapped[Sha256] = mapped_column(nullable=False)

    occurred_at: Mapped[Timestamp] = created_at_column()

    __table_args__ = (
        Index("ix_audit_events_job_id_id", "job_id", "id"),
        Index("ix_audit_events_request_id_id", "request_id", "id"),
        Index("ix_audit_events_subject_id", "subject_kind", "subject_id", "id"),
        Index("ix_audit_events_occurred_at", "occurred_at"),
    )

    @classmethod
    def create_linked(
        cls,
        *,
        actor: str,
        event_type: str,
        payload: dict[str, Any],
        previous: AuditEvent | None,
        job_id: uuid.UUID | None = None,
        request_id: uuid.UUID | None = None,
    ) -> AuditEvent:
        """Build an event linked to ``previous``.

        Always use this rather than constructing directly: it is what guarantees
        ``this_hash`` is computed over the same canonical form the verifier will later
        recompute. A hand-built row with a hand-written hash is how a chain silently stops
        verifying.
        """
        prev_hash = previous.this_hash if previous is not None else None
        return cls(
            actor=actor,
            event_type=event_type,
            payload=payload,
            job_id=job_id,
            request_id=request_id,
            prev_hash=prev_hash,
            this_hash=chain_hash(prev_hash, payload),
        )
