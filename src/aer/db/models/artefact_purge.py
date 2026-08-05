"""A payload erased under a licence obligation, recorded as an event rather than a flag.

**Why a table and not a column.** ``artefacts`` rejects every UPDATE with a database trigger,
and that is not incidental — it is invariant 1 expressed in the schema. Marking a row as
purged would have meant relaxing the trigger to allow *some* columns to change, which turns a
rule anybody can state into a rule anybody has to read carefully.

A purge is also genuinely an event: it happened, at a time, because of a licence, at somebody's
instruction. Appending a row says that; setting a flag says only that it is now true.

**What survives a purge, and it is nearly everything.** The artefact row, its SHA-256, its
size, its media type, its storage key, every ``source_documents`` row pointing at it and every
citation resolved against it. What goes is the payload. So the provenance chain stays intact
and answerable — *which* bytes, from *where*, at *what* time, hashing to *what* — and the one
question that stops being answerable is "show me those bytes again".

That is a real loss and ADR 0031 says so rather than engineering around it: a citation into a
purged artefact can no longer be **re-verified**, only shown to have been verified, on a date,
against a hash. The alternative was not keeping the bytes; it was not having the source.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aer.db.base import Base, created_at_column
from aer.db.types import Timestamp, UuidFk, UuidPk

if TYPE_CHECKING:
    from aer.db.models.artefact import Artefact

__all__ = ["ArtefactPurge"]


class ArtefactPurge(Base):
    __tablename__ = "artefact_purges"

    id: Mapped[UuidPk]

    # RESTRICT, not CASCADE. Deleting the artefact row would take the record of its erasure
    # with it, leaving a citation pointing at nothing with no explanation — which is the exact
    # state this table exists to make impossible.
    artefact_id: Mapped[UuidFk] = mapped_column(
        ForeignKey("artefacts.id", ondelete="RESTRICT"), nullable=False, unique=True
    )

    # Why the bytes went. Free text, and required: "licence" is not a reason, "the EODHD
    # subscription ended on 2027-03-01 and the agreement requires deletion within a month" is.
    reason: Mapped[str] = mapped_column(Text, nullable=False)

    # The licence note as it stood when the artefact was acquired, copied here. The policy
    # that obliged the deletion may itself change afterwards, and a purge has to be defensible
    # against the terms in force at the time rather than against today's.
    licence_note: Mapped[str] = mapped_column(Text, nullable=False)

    # Who instructed it. Nullable and not a foreign key for the same reason `audit_events`
    # does it: a retention sweep run by a scheduled job has no user, and a purge must survive
    # the deletion of the account that ordered it.
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))

    bytes_freed: Mapped[int] = mapped_column(BigInteger, nullable=False)

    purged_at: Mapped[Timestamp] = created_at_column()

    artefact: Mapped[Artefact] = relationship(back_populates="purge")

    __table_args__ = (
        CheckConstraint("char_length(reason) > 0", name="artefact_purge_states_a_reason"),
        CheckConstraint("bytes_freed >= 0", name="artefact_purge_freed_is_not_negative"),
        Index("ix_artefact_purges_purged_at", "purged_at"),
    )

    def __repr__(self) -> str:
        return f"<ArtefactPurge {self.artefact_id} at {self.purged_at}>"
