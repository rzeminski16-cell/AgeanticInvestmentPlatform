"""Checking that the audit log still says what it said.

The chain is written on every event by :meth:`AuditEvent.create_linked` and, until this
module, nothing ever read it back. A tamper-evident log nobody verifies is a log with the
*cost* of tamper-evidence and none of the benefit: the property is not "the rows are
linked", it is "somebody would notice", and the second one needs a reader.

**What this can and cannot tell you.** Anyone with write access to the database can rewrite
the whole table, recomputing every hash as they go, and this will report a sound chain. What
it catches is the realistic case — a row edited, deleted, inserted or reordered in place,
by hand or by a bug — where the hashes after the change no longer follow. That is the
achievable property, and :mod:`aer.db.models.audit_event` is candid about it.
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.core.hashing import find_chain_break
from aer.db.models import AuditEvent

__all__ = ["DEFAULT_BATCH_SIZE", "ChainReport", "verify_audit_chain"]

_log = structlog.get_logger("aer.services.audit_verify")

# Rows held in memory at once. The log grows without bound — it is append-only by design and
# nothing prunes it — so this is read in pages rather than loaded whole.
DEFAULT_BATCH_SIZE = 1_000


@dataclass(frozen=True, slots=True)
class ChainReport:
    """What a walk of the audit chain found.

    ``broken_at_id`` is the id of the first record that failed, not its position: an
    operator can select that row and look at it, which a zero-based index into a batch does
    not let them do.
    """

    checked: int
    total: int
    broken_at_id: int | None = None
    reason: str | None = None

    @property
    def is_sound(self) -> bool:
        return self.broken_at_id is None

    @property
    def is_empty(self) -> bool:
        return self.total == 0


async def verify_audit_chain(
    session: AsyncSession, *, batch_size: int = DEFAULT_BATCH_SIZE
) -> ChainReport:
    """Walk the audit log in id order and check every link.

    Stops at the first break. There is no value in counting the rest: every record after a
    break fails too, by construction, so a full tally would report thousands of failures
    for one edit and bury the only line that matters.

    **The genesis record is checked as well.** A chain whose first row carries a
    ``prev_hash`` has been re-rooted — the natural shape of "delete the beginning of the log
    and make the remainder self-consistent" — and every per-record hash would still verify,
    so nothing else here would notice.
    """
    total = await session.scalar(select(func.count()).select_from(AuditEvent)) or 0
    if total == 0:
        return ChainReport(checked=0, total=0)

    checked = 0
    anchor: str | None = None
    last_id = 0

    while True:
        rows = list(
            await session.scalars(
                select(AuditEvent)
                .where(AuditEvent.id > last_id)
                .order_by(AuditEvent.id)
                .limit(batch_size)
            )
        )
        if not rows:
            break

        if checked == 0 and rows[0].prev_hash is not None:
            return ChainReport(
                checked=0,
                total=total,
                broken_at_id=rows[0].id,
                reason=(
                    "the first record in the log links to a predecessor that is not there, "
                    "so the beginning of the chain has been removed or replaced"
                ),
            )

        break_index = find_chain_break(rows, expected_previous=anchor)
        if break_index is not None:
            broken = rows[break_index]
            # What this record should have linked to: the anchor carried in from the last
            # page when the break is at the seam, otherwise its neighbour in this one.
            # Getting this wrong does not hide the break, but it does misdiagnose it, and
            # "the record was edited" sends an operator looking for the wrong thing.
            preceding = anchor if break_index == 0 else rows[break_index - 1].this_hash
            return ChainReport(
                checked=checked + break_index,
                total=total,
                broken_at_id=broken.id,
                reason=_why(broken, expected_previous=preceding),
            )

        checked += len(rows)
        anchor = rows[-1].this_hash
        last_id = rows[-1].id

    _log.info("audit_chain.verified", checked=checked)
    return ChainReport(checked=checked, total=total)


def _why(broken: AuditEvent, *, expected_previous: str | None) -> str:
    """Which of the two failures this is, in words an operator can act on."""
    if expected_previous is not None and broken.prev_hash != expected_previous:
        return (
            "this record does not link to the one before it, so a record was inserted, "
            "removed or reordered here"
        )
    return (
        "this record's hash does not match its own contents, so the record itself was "
        "edited after it was written"
    )
