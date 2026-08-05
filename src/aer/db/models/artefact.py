"""A stored artefact: the bytes behind a piece of evidence.

One row per distinct content, keyed by the SHA-256 of that content. Two fetches of the
same filing produce one row and one file — deduplication is not a feature that had to be
built, it falls out of addressing by digest.

**These rows are immutable, and the database enforces it.** A trigger rejects every
UPDATE. That is not belt-and-braces over a service layer that already avoids updating:
the whole claim of this platform is that a report's evidence can be checked later, and a
row whose ``sha256`` could be edited to point at different bytes would make that claim
unverifiable. A rule that lives only in Python is a rule that a migration, a script or a
late-night ``psql`` session does not have.

DELETE is deliberately *not* blocked. Retention and erasure are legitimate operations —
they simply have no path in the service layer, so they cannot happen by accident. See
``docs/adr/0008-content-addressed-immutable-artefacts.md``.

**Erasure arrived in ADR 0031**, and it does not touch these rows. A licensed payload whose
subscription has ended is purged from the *store*; the row, its hash and everything pointing
at it survive, and the erasure is appended to ``artefact_purges``. The trigger above therefore
still rejects every UPDATE, which is what it was for.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, CheckConstraint, Index, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aer.db.base import Base, created_at_column
from aer.db.types import Sha256, Timestamp, UuidPk

if TYPE_CHECKING:
    from aer.db.models.artefact_purge import ArtefactPurge
    from aer.db.models.source_document import SourceDocument

__all__ = ["Artefact"]


class Artefact(Base):
    __tablename__ = "artefacts"

    id: Mapped[UuidPk]

    # The content address. Unique because it *is* the identity: the same bytes are the
    # same artefact, however many times they arrive.
    sha256: Mapped[Sha256] = mapped_column(nullable=False, unique=True)

    media_type: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)

    # Which backend holds the bytes, and where within it. Recorded rather than derived so
    # that artefacts stored before a backend change remain findable afterwards.
    storage_backend: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'local'")
    )
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped[Timestamp] = created_at_column()

    sources: Mapped[list[SourceDocument]] = relationship(back_populates="artefact")

    # The erasure record, if the payload has been purged under a licence obligation. A
    # relationship rather than a column, because `artefacts` rejects every UPDATE — see
    # `aer.db.models.artefact_purge` on why a purge is an event and not a flag.
    purge: Mapped[ArtefactPurge | None] = relationship(back_populates="artefact", uselist=False)

    @property
    def is_purged(self) -> bool:
        """Whether the payload has been erased. The row and its lineage remain either way."""
        return self.purge is not None

    __table_args__ = (
        # A zero-byte artefact is almost always a failed fetch that was stored anyway.
        # Every empty file hashes to the same digest, so one would silently deduplicate
        # against every other, and a citation pointing at "the empty artefact" would
        # verify against nothing at all.
        CheckConstraint("size_bytes > 0", name="artefact_is_not_empty"),
        CheckConstraint("char_length(sha256) = 64", name="artefact_sha256_is_full_length"),
        CheckConstraint("sha256 = lower(sha256)", name="artefact_sha256_is_lowercase"),
        Index("ix_artefacts_created_at", "created_at"),
    )
