"""The artefact store interface.

A ``Protocol`` rather than an abstract base class: implementations are structural, so a
test double satisfies it without inheriting from anything, and a future S3 backend does
not have to import this module to be usable.

Deliberately narrow. There is no ``delete``, no ``update`` and no ``move``. Those are not
oversights — an artefact's address is the hash of its content, so an artefact that
changes is a different artefact, and one that vanishes takes a report's evidence with it.
Retention and erasure, when they arrive, will be a deliberate operation with its own
audit trail, not a method on this interface.

Everything is async because callers are, and because a 50 MiB read must not block the
event loop. Implementations are expected to do their blocking work in a thread.
"""

from __future__ import annotations

from collections.abc import AsyncIterable, AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

__all__ = ["ArtefactStore", "StoredArtefact"]


@dataclass(frozen=True, slots=True)
class StoredArtefact:
    """The outcome of storing bytes.

    ``was_new`` is what makes deduplication observable. Storing the same filing twice is
    not an error and not a second copy; the caller needs to know which happened so it can
    reuse the existing database row rather than creating a duplicate.
    """

    sha256: str
    size_bytes: int
    was_new: bool


@runtime_checkable
class ArtefactStore(Protocol):
    """Content-addressed, write-once storage for fetched bytes."""

    async def put_bytes(self, data: bytes) -> StoredArtefact:
        """Store ``data`` and return its content address.

        Raises:
            ValidationError: If the payload exceeds the configured size cap.
            IntegrityError: If the bytes read back do not match what was written.
        """
        ...

    async def put_stream(self, chunks: AsyncIterable[bytes]) -> StoredArtefact:
        """Store a stream without holding all of it in memory.

        The size cap is enforced *while* consuming the stream, so an oversized or
        never-ending response is abandoned partway rather than after it has been fully
        received. That is the difference between a cap and a report.
        """
        ...

    async def read(self, sha256: str) -> bytes:
        """Return the stored bytes for a content address."""
        ...

    def open(self, sha256: str) -> AsyncIterator[bytes]:
        """Yield the stored bytes in chunks, for artefacts too large to hold at once."""
        ...

    async def exists(self, sha256: str) -> bool:
        """Whether an artefact is present. Does not verify its integrity."""
        ...

    async def verify(self, sha256: str) -> int:
        """Re-read the artefact and confirm it still hashes to its own address.

        Returns its size in bytes.

        Raises:
            IntegrityError: If the artefact is missing or its content has changed. Never
                returns false for a corrupt artefact — a caller that has to remember to
                check a boolean is a caller that will one day forget.
        """
        ...

    def path_for(self, sha256: str) -> Path:
        """Where an artefact lives. Synchronous: it computes a path, it touches nothing."""
        ...

    def storage_key_for(self, sha256: str) -> str:
        """The backend-relative locator recorded on the artefact row.

        Relative, not absolute, and deliberately not a filesystem path in the interface:
        moving the artefact directory — to a bigger disk, into a backup, onto another
        machine — must not invalidate every row, and an object-store backend has keys
        rather than paths. The absolute location is a property of the installation; the
        key is a property of the artefact.
        """
        ...
