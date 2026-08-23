"""Erasing licensed bytes, as a capability the ordinary storage path does not have.

:mod:`aer.storage.protocol` says it plainly: *"Retention and erasure, when they arrive, will
be a deliberate operation with its own audit trail, not a method on this interface."* This is
that operation, and it is a **separate protocol on purpose**.

A service holding an :class:`~aer.storage.protocol.ArtefactStore` cannot delete anything —
not because it politely does not, but because the type it holds has no method for it. Only a
caller that asks for :class:`PurgeableStore` explicitly can erase, and in this codebase that
is exactly one module: :mod:`aer.services.retention`. It is the same shape as
:class:`~aer.core.sectors.ValuationMandate` (ADR 0029) — a capability you must be handed
rather than a rule you must remember.

**Why this exists at all.** EODHD's subscription agreement obliges the subscriber to delete
every copy within a month of the subscription ending. An immutable, no-delete store is
precisely a store that cannot comply. `docs/archive/PLAN.md` risk T16 already called for a retention
policy; a licensed feed is simply the first source that makes it load-bearing rather than
prudent. ADR 0031 has the reasoning and what is lost.

**The bytes go; the provenance stays.** Purging removes the payload from the backend and
nothing else. The artefact row, its hash, its size, its media type, every source document
pointing at it and every citation resolved against it survive untouched — and a purge is
recorded as its own append-only row, so "why is this evidence gone?" has an answer with a
date, an actor and a reason on it.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

__all__ = ["PurgeableStore"]


@runtime_checkable
class PurgeableStore(Protocol):
    """A store whose payloads can be erased under a licence obligation.

    Structural, like :class:`~aer.storage.protocol.ArtefactStore`, so an implementation
    satisfies both without inheriting from either — and so a caller can be given the read
    interface alone, which is the point.
    """

    async def purge(self, sha256: str) -> int:
        """Erase the bytes at this content address. Returns how many were freed.

        **Idempotent, and returns zero for an artefact that is not there.** A retention sweep
        that has already run, or a payload removed by hand, must not turn a compliance
        operation into an error — the obligation is that the bytes are gone, and they are.

        Does not touch the database. The row, the hash and the lineage are somebody else's
        concern precisely because they are *not* being deleted.

        Raises:
            ValidationError: If the address is not a well-formed digest. A malformed key
                would otherwise resolve to a path outside the store.
        """
        ...
