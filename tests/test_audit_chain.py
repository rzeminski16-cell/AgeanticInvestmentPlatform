"""Verifying the audit chain, and the ways a verifier can look like it works.

Gap A11: the chain has been written on every event since Task 3 and read by nothing. The
risk in closing it is a verifier that passes on everything — which is indistinguishable
from a sound log until the day it matters — so most of what follows is tampering, each
kind done to a real database and each expected to be caught.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from aer.core.enums import Decision, GateKind, JobStatus, RequestStatus, UserRole
from aer.core.hashing import chain_hash, find_chain_break
from aer.db.models import AuditEvent, Job, ResearchRequest, User
from aer.services.approvals import payload_hash_for, record_decision
from aer.services.audit_verify import verify_audit_chain


class _Link:
    """A chain link with no database behind it, for the pure-function tests."""

    def __init__(self, payload: Any, prev_hash: str | None, this_hash: str | None = None) -> None:
        self.payload = payload
        self.prev_hash = prev_hash
        self.this_hash = this_hash if this_hash is not None else chain_hash(prev_hash, payload)


def _sound_links(count: int) -> list[_Link]:
    links: list[_Link] = []
    previous: str | None = None
    for index in range(count):
        link = _Link({"n": index}, previous)
        links.append(link)
        previous = link.this_hash
    return links


class TestTheSliceAnchor:
    """`expected_previous`, which exists so a paged walk cannot miss a break at the seam."""

    def test_a_sound_chain_has_no_break(self) -> None:
        assert find_chain_break(_sound_links(5)) is None

    def test_a_sound_slice_verifies_against_its_anchor(self) -> None:
        links = _sound_links(6)

        assert find_chain_break(links[3:], expected_previous=links[2].this_hash) is None

    def test_a_slice_whose_anchor_does_not_match_breaks_at_its_first_record(self) -> None:
        links = _sound_links(6)

        assert find_chain_break(links[3:], expected_previous="0" * 64) == 0

    def test_without_an_anchor_the_first_record_of_a_slice_is_unchecked(self) -> None:
        """The behaviour the anchor exists to correct, pinned so it cannot drift back.

        A slice starting mid-chain has no predecessor of its own to compare against, so
        with no anchor supplied its first record's `prev_hash` goes unexamined. That is
        correct for the true start of a log and wrong for page two, which is exactly the
        confusion `verify_audit_chain` has to avoid.
        """
        links = _sound_links(6)

        assert find_chain_break(links[3:]) is None


@pytest.mark.usefixtures("db_session")
class TestTheChainOverARealLog:
    """Against Postgres, because JSONB is where a hash over a payload can quietly change."""

    async def _write(self, session: AsyncSession, count: int) -> list[AuditEvent]:
        written: list[AuditEvent] = []
        previous: AuditEvent | None = None
        for index in range(count):
            event = AuditEvent.create_linked(
                actor="tester",
                event_type="test.event",
                payload={"n": index, "note": f"event {index}"},
                previous=previous,
            )
            session.add(event)
            await session.flush()
            written.append(event)
            previous = event
        return written

    async def test_an_empty_log_is_reported_as_empty_not_as_sound(
        self, db_session: AsyncSession
    ) -> None:
        """Nothing to verify and everything verified are different answers.

        A cron line that printed "the chain is intact" against a table somebody had just
        truncated would be reassuring at precisely the wrong moment.
        """
        await db_session.execute(text("DELETE FROM audit_events"))

        report = await verify_audit_chain(db_session)

        assert report.is_empty
        assert report.is_sound
        assert report.checked == 0

    async def test_a_log_written_through_create_linked_verifies(
        self, db_session: AsyncSession
    ) -> None:
        await db_session.execute(text("DELETE FROM audit_events"))
        await self._write(db_session, 12)

        report = await verify_audit_chain(db_session)

        assert report.is_sound
        assert report.checked == 12
        assert report.broken_at_id is None

    async def test_a_payload_round_tripped_through_jsonb_still_verifies(
        self, db_session: AsyncSession
    ) -> None:
        """JSONB does not preserve key order and normalises its input.

        The hash is taken over `canonical_json`, which sorts keys, so a payload written in
        one order and read back in another must still verify. If it did not, every chain
        would break the moment it was read from the database rather than from memory —
        the verifier would be useless and would look like a tampered log.
        """
        await db_session.execute(text("DELETE FROM audit_events"))
        event = AuditEvent.create_linked(
            actor="tester",
            event_type="test.event",
            payload={"zebra": 1, "alpha": {"nested": [3, 2, 1]}, "middle": "x"},
            previous=None,
        )
        db_session.add(event)
        await db_session.flush()
        db_session.expunge_all()

        report = await verify_audit_chain(db_session)

        assert report.is_sound

    async def test_an_edited_payload_is_caught_and_named(self, db_session: AsyncSession) -> None:
        await db_session.execute(text("DELETE FROM audit_events"))
        written = await self._write(db_session, 8)
        target = written[4]
        await db_session.execute(
            text("UPDATE audit_events SET payload = '{\"n\": 999}'::jsonb WHERE id = :id"),
            {"id": target.id},
        )
        db_session.expunge_all()

        report = await verify_audit_chain(db_session)

        assert not report.is_sound
        assert report.broken_at_id == target.id
        assert "edited" in (report.reason or "")

    async def test_a_deleted_record_is_caught(self, db_session: AsyncSession) -> None:
        """The link check, not the content check: every surviving row hashes correctly."""
        await db_session.execute(text("DELETE FROM audit_events"))
        written = await self._write(db_session, 8)
        await db_session.execute(
            text("DELETE FROM audit_events WHERE id = :id"), {"id": written[3].id}
        )
        db_session.expunge_all()

        report = await verify_audit_chain(db_session)

        assert not report.is_sound
        assert report.broken_at_id == written[4].id
        assert "inserted, removed or reordered" in (report.reason or "")

    async def test_a_break_at_a_page_boundary_is_caught(self, db_session: AsyncSession) -> None:
        """The bug this verifier was written to avoid.

        Verifying page by page, the first record of each page has no predecessor inside its
        own batch. A verifier that simply called `find_chain_break` per page would skip
        exactly that record's link check, so a row deleted at the seam would verify — and
        a tamperer choosing where to cut is not a far-fetched threat model.
        """
        await db_session.execute(text("DELETE FROM audit_events"))
        written = await self._write(db_session, 10)
        # Which row to delete takes care: removing one shifts every later row up a place,
        # so the gap has to be placed such that the *survivor* after it lands at index 0 of
        # page two. Deleting written[5] leaves 0-4 filling the first page of five and puts
        # written[6] — whose prev_hash points at the row now gone — first on page two.
        # Deleting anything earlier moves the break into the middle of a page, where the
        # ordinary in-page check finds it and the seam is never exercised.
        await db_session.execute(
            text("DELETE FROM audit_events WHERE id = :id"), {"id": written[5].id}
        )
        db_session.expunge_all()

        report = await verify_audit_chain(db_session, batch_size=5)

        assert not report.is_sound
        assert report.broken_at_id == written[6].id
        assert "inserted, removed or reordered" in (report.reason or "")

    async def test_a_truncated_beginning_is_caught(self, db_session: AsyncSession) -> None:
        """Every remaining row is self-consistent; only the missing root gives it away.

        Deleting the start of the log and leaving the rest is the one tampering that
        survives both per-record checks, because the surviving records still link to each
        other correctly. The genesis check is the only thing that sees it.
        """
        await db_session.execute(text("DELETE FROM audit_events"))
        written = await self._write(db_session, 6)
        await db_session.execute(
            text("DELETE FROM audit_events WHERE id <= :id"), {"id": written[1].id}
        )
        db_session.expunge_all()

        report = await verify_audit_chain(db_session)

        assert not report.is_sound
        assert report.broken_at_id == written[2].id
        assert "beginning of the chain" in (report.reason or "")

    async def test_the_count_verified_before_a_break_is_reported(
        self, db_session: AsyncSession
    ) -> None:
        """How much of the log an operator can still believe."""
        await db_session.execute(text("DELETE FROM audit_events"))
        written = await self._write(db_session, 9)
        await db_session.execute(
            text("UPDATE audit_events SET payload = '{\"n\": -1}'::jsonb WHERE id = :id"),
            {"id": written[6].id},
        )
        db_session.expunge_all()

        report = await verify_audit_chain(db_session)

        assert report.checked == 6
        assert report.total == 9

    async def test_the_walk_pages_without_changing_its_answer(
        self, db_session: AsyncSession
    ) -> None:
        """A sound log verifies whole at every batch size, including one row at a time."""
        await db_session.execute(text("DELETE FROM audit_events"))
        await self._write(db_session, 11)

        for size in (1, 2, 3, 11, 50):
            report = await verify_audit_chain(db_session, batch_size=size)
            assert report.is_sound, size
            assert report.checked == 11, size

    async def test_events_written_by_the_application_verify(self, db_session: AsyncSession) -> None:
        """Through `record_decision`, not through this test's own helper.

        The chain is only worth anything if it holds over the payloads the application
        actually writes — UUID strings, enum values, a hex digest — rather than the flat
        integers a test reaches for. Writing them here through the real approvals path is
        what makes that more than an assumption.
        """
        await db_session.execute(text("DELETE FROM audit_events"))
        user = User(email="audit@example.invalid", display_name="Audit", role=UserRole.OWNER)
        db_session.add(user)
        await db_session.flush()
        request = ResearchRequest(
            user_id=user.id,
            company_name="Microsoft Corporation",
            ticker="MSFT",
            exchange="NASDAQ",
            as_of_date=date(2023, 1, 1),
            base_currency="USD",
            investment_horizon_months=36,
            max_cost_gbp="2.00",
            portfolio_context={},
            point_in_time=True,
            status=RequestStatus.DRAFT,
        )
        db_session.add(request)
        await db_session.flush()
        job = Job(
            work_order_id=request.id,
            request_id=request.id,
            workflow_version="test-1",
            code_version="a1b2c3d4",
            status=JobStatus.RUNNING,
        )
        db_session.add(job)
        await db_session.flush()

        await record_decision(
            db_session,
            job=job,
            gate=GateKind.PLAN,
            decision=Decision.APPROVED,
            actor=user,
            payload_hash=payload_hash_for({"plan": "agreed"}),
        )
        db_session.expunge_all()

        report = await verify_audit_chain(db_session)

        assert report.is_sound
        assert report.checked == 1
