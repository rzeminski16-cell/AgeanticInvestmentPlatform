"""Erasure: what it removes, what it must never remove, and what it costs.

This is the one module that destroys evidence, so the tests that matter most are the ones
asserting it *refuses*. A retention sweep that erased a 10-K would be destroying the thing
this platform exists to preserve, and it would look exactly like a sweep that worked.

The other claim under test is subtler: a purge takes the bytes and nothing else. The artefact
row, its hash, its size, every source document pointing at it and every citation resolved
against it all survive. What is lost is re-verification — a citation can still be shown to
*have been* verified against a hash on a date, and can never be checked again. ADR 0031 says
so rather than engineering around it, and `test_the_lineage_survives_the_bytes` is what keeps
that true.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import select, text

from aer.core.enums import Provider, SourceTier, UserRole
from aer.db.models import (
    Artefact,
    ArtefactPurge,
    AuditEvent,
    ResearchRequest,
    SourceDocument,
    User,
)
from aer.errors import ValidationError
from aer.fetch.policy import DEFAULT_POLICIES, RetentionClass
from aer.services import retention as retention_service
from aer.services.retention import PermanentArtefactError
from aer.storage.protocol import ArtefactStore
from aer.storage.retention import PurgeableStore
from tests.workflow_fixtures import AS_OF_DATE

pytestmark = pytest.mark.integration

_TABLES = "research_requests, audit_events, users, artefacts, prompts, companies"

PRICES = b'{"code":"MSFT.US","prices":[{"date":"2024-06-28","close":446.95}]}'
FILING = b'{"cik":"0000789019","facts":{"us-gaap":{}}}'


@pytest.fixture
async def scene(db_session: Any, artefact_store: Any) -> dict[str, Any]:
    await db_session.execute(text(f"TRUNCATE {_TABLES} RESTART IDENTITY CASCADE"))

    operator = User(email="operator@example.invalid", display_name="Operator", role=UserRole.OWNER)
    db_session.add(operator)
    await db_session.flush()

    request = ResearchRequest(
        user_id=operator.id,
        company_name="Microsoft Corporation",
        ticker="MSFT",
        exchange="NASDAQ",
        as_of_date=AS_OF_DATE,
        point_in_time=True,
        base_currency="USD",
        reporting_currency="USD",
        investment_horizon_months=12,
        max_cost_gbp="2.50",
    )
    db_session.add(request)
    await db_session.flush()

    return {"store": artefact_store, "operator": operator, "request": request}


async def store_document(
    session: Any,
    scene: dict[str, Any],
    *,
    payload: bytes,
    provider: Provider,
    tier: SourceTier,
) -> tuple[Artefact, SourceDocument]:
    """An artefact with a source document, the way an acquisition would leave them."""
    store = scene["store"]
    stored = await store.put_bytes(payload)
    artefact = Artefact(
        sha256=stored.sha256,
        media_type="application/json",
        size_bytes=stored.size_bytes,
        storage_key=store.storage_key_for(stored.sha256),
    )
    session.add(artefact)
    await session.flush()

    document = SourceDocument(
        artefact_id=artefact.id,
        request_id=scene["request"].id,
        provider=provider,
        source_tier=tier,
        url="https://example.invalid/data",
        title="Test document",
        retrieved_at=datetime.now(UTC),
        licence_note=DEFAULT_POLICIES[provider].licence_note,
    )
    session.add(document)
    await session.flush()
    return artefact, document


# -- What must never be purged ---------------------------------------------------------------


class TestPermanentEvidenceIsRefused:
    """The refusals that protect the archive from its own retention machinery."""

    async def test_a_filing_cannot_be_purged(self, db_session, scene):
        artefact, _ = await store_document(
            db_session,
            scene,
            payload=FILING,
            provider=Provider.SEC_EDGAR,
            tier=SourceTier.T1_REGULATORY,
        )

        with pytest.raises(PermanentArtefactError, match="no licence asks to be destroyed"):
            await retention_service.purge_artefact(
                db_session,
                scene["store"],
                artefact=artefact,
                reason="Tidying up.",
                actor=scene["operator"],
            )

    async def test_the_bytes_are_still_there_after_the_refusal(self, db_session, scene):
        """A refusal that had already deleted would be worse than no refusal."""
        artefact, _ = await store_document(
            db_session,
            scene,
            payload=FILING,
            provider=Provider.SEC_EDGAR,
            tier=SourceTier.T1_REGULATORY,
        )

        with pytest.raises(PermanentArtefactError):
            await retention_service.purge_artefact(
                db_session,
                scene["store"],
                artefact=artefact,
                reason="Tidying up.",
                actor=scene["operator"],
            )

        assert await scene["store"].read(artefact.sha256) == FILING

    async def test_asking_for_a_permanent_provider_s_purgeable_set_is_refused(self, db_session):
        """ "Which SEC artefacts may I delete?" is a question with one safe answer."""
        with pytest.raises(PermanentArtefactError, match="retained permanently"):
            await retention_service.purgeable_artefacts(db_session, provider=Provider.SEC_EDGAR)

    @pytest.mark.parametrize(
        "provider",
        [Provider.SEC_EDGAR, Provider.COMPANIES_HOUSE, Provider.FRED, Provider.ONS],
    )
    def test_the_public_sources_are_all_permanent(self, provider):
        assert DEFAULT_POLICIES[provider].retention is RetentionClass.PERMANENT

    def test_eodhd_is_the_only_licensed_one(self):
        """If a second paid feed is added, this fails until somebody classifies it."""
        assert retention_service.licensed_providers() == (Provider.EODHD,)


# -- What may be purged, and what survives it ------------------------------------------------


class TestPurgingALicensedPayload:
    async def test_the_bytes_go(self, db_session, scene):
        artefact, _ = await store_document(
            db_session,
            scene,
            payload=PRICES,
            provider=Provider.EODHD,
            tier=SourceTier.T4_LICENSED_MARKET,
        )

        await retention_service.purge_artefact(
            db_session,
            scene["store"],
            artefact=artefact,
            reason="Subscription ended 2027-03-01; agreement requires deletion within a month.",
            actor=scene["operator"],
        )

        assert not await scene["store"].exists(artefact.sha256)

    async def test_the_lineage_survives_the_bytes(self, db_session, scene):
        """The whole claim of the split. Everything except the payload is still there."""
        artefact, document = await store_document(
            db_session,
            scene,
            payload=PRICES,
            provider=Provider.EODHD,
            tier=SourceTier.T4_LICENSED_MARKET,
        )
        original_hash = artefact.sha256

        await retention_service.purge_artefact(
            db_session,
            scene["store"],
            artefact=artefact,
            reason="Subscription ended.",
            actor=scene["operator"],
        )

        still_there = await db_session.get(Artefact, artefact.id)
        assert still_there is not None
        assert still_there.sha256 == original_hash
        assert still_there.size_bytes == len(PRICES)
        assert still_there.storage_key

        source = await db_session.get(SourceDocument, document.id)
        assert source is not None
        assert source.provider is Provider.EODHD

    async def test_the_purge_is_recorded_with_a_reason_and_an_actor(self, db_session, scene):
        artefact, _ = await store_document(
            db_session,
            scene,
            payload=PRICES,
            provider=Provider.EODHD,
            tier=SourceTier.T4_LICENSED_MARKET,
        )

        purge = await retention_service.purge_artefact(
            db_session,
            scene["store"],
            artefact=artefact,
            reason="Subscription ended 2027-03-01.",
            actor=scene["operator"],
        )

        assert purge.actor == "operator@example.invalid"
        assert purge.actor_user_id == scene["operator"].id
        assert "2027-03-01" in purge.reason
        assert purge.bytes_freed == len(PRICES)

    async def test_the_licence_in_force_at_acquisition_is_copied_onto_the_record(
        self, db_session, scene
    ):
        """A purge is defensible against the terms it was acquired under, not today's."""
        artefact, document = await store_document(
            db_session,
            scene,
            payload=PRICES,
            provider=Provider.EODHD,
            tier=SourceTier.T4_LICENSED_MARKET,
        )
        document.licence_note = "The terms as they stood in 2026."
        await db_session.flush()

        purge = await retention_service.purge_artefact(
            db_session,
            scene["store"],
            artefact=artefact,
            reason="Subscription ended.",
            actor=scene["operator"],
        )

        assert purge.licence_note == "The terms as they stood in 2026."

    async def test_it_writes_an_audit_event(self, db_session, scene):
        """Deleting evidence is exactly the sort of thing the chain exists for."""
        artefact, _ = await store_document(
            db_session,
            scene,
            payload=PRICES,
            provider=Provider.EODHD,
            tier=SourceTier.T4_LICENSED_MARKET,
        )

        await retention_service.purge_artefact(
            db_session,
            scene["store"],
            artefact=artefact,
            reason="Subscription ended.",
            actor=scene["operator"],
        )

        event = await db_session.scalar(
            select(AuditEvent)
            .where(AuditEvent.event_type == "artefact.purged")
            .order_by(AuditEvent.id.desc())
            .limit(1)
        )
        assert event is not None
        assert event.payload["sha256"] == artefact.sha256
        assert event.payload["provider"] == "eodhd"
        assert event.actor == "operator@example.invalid"

    async def test_the_artefact_reports_itself_purged(self, db_session, scene):
        artefact, _ = await store_document(
            db_session,
            scene,
            payload=PRICES,
            provider=Provider.EODHD,
            tier=SourceTier.T4_LICENSED_MARKET,
        )

        await retention_service.purge_artefact(
            db_session,
            scene["store"],
            artefact=artefact,
            reason="Subscription ended.",
            actor=scene["operator"],
        )
        await db_session.refresh(artefact, ["purge"])

        assert artefact.is_purged


# -- The guards on the operation itself ------------------------------------------------------


class TestTheOperationRefusesToBeVague:
    async def test_a_blank_reason_is_refused(self, db_session, scene):
        artefact, _ = await store_document(
            db_session,
            scene,
            payload=PRICES,
            provider=Provider.EODHD,
            tier=SourceTier.T4_LICENSED_MARKET,
        )

        with pytest.raises(ValidationError, match="destroys evidence and explains nothing"):
            await retention_service.purge_artefact(
                db_session,
                scene["store"],
                artefact=artefact,
                reason="   ",
                actor=scene["operator"],
            )

        assert await scene["store"].exists(artefact.sha256)

    async def test_purging_twice_is_refused(self, db_session, scene):
        artefact, _ = await store_document(
            db_session,
            scene,
            payload=PRICES,
            provider=Provider.EODHD,
            tier=SourceTier.T4_LICENSED_MARKET,
        )
        await retention_service.purge_artefact(
            db_session,
            scene["store"],
            artefact=artefact,
            reason="Subscription ended.",
            actor=scene["operator"],
        )

        with pytest.raises(ValidationError, match="already purged"):
            await retention_service.purge_artefact(
                db_session,
                scene["store"],
                artefact=artefact,
                reason="Again.",
                actor=scene["operator"],
            )

    async def test_the_store_purge_itself_is_idempotent(self, db_session, scene):
        """A sweep must be safe to re-run: the obligation is absence, not authorship."""
        stored = await scene["store"].put_bytes(PRICES)

        first = await scene["store"].purge(stored.sha256)
        second = await scene["store"].purge(stored.sha256)

        assert first == len(PRICES)
        assert second == 0


# -- The whole-provider sweep ----------------------------------------------------------------


class TestSweepingAProvider:
    async def test_it_purges_every_outstanding_payload(self, db_session, scene):
        for index in range(3):
            await store_document(
                db_session,
                scene,
                payload=PRICES + str(index).encode(),
                provider=Provider.EODHD,
                tier=SourceTier.T4_LICENSED_MARKET,
            )

        outcome = await retention_service.purge_provider(
            db_session,
            scene["store"],
            provider=Provider.EODHD,
            reason="Subscription terminated.",
            actor=scene["operator"],
        )

        assert outcome.purged == 3
        assert outcome.bytes_freed > 0
        assert (
            await retention_service.purgeable_artefacts(db_session, provider=Provider.EODHD) == []
        )

    async def test_it_leaves_other_providers_alone(self, db_session, scene):
        filing, _ = await store_document(
            db_session,
            scene,
            payload=FILING,
            provider=Provider.SEC_EDGAR,
            tier=SourceTier.T1_REGULATORY,
        )
        await store_document(
            db_session,
            scene,
            payload=PRICES,
            provider=Provider.EODHD,
            tier=SourceTier.T4_LICENSED_MARKET,
        )

        await retention_service.purge_provider(
            db_session,
            scene["store"],
            provider=Provider.EODHD,
            reason="Subscription terminated.",
            actor=scene["operator"],
        )

        assert await scene["store"].read(filing.sha256) == FILING

    async def test_a_second_sweep_finds_nothing_and_does_not_fail(self, db_session, scene):
        await store_document(
            db_session,
            scene,
            payload=PRICES,
            provider=Provider.EODHD,
            tier=SourceTier.T4_LICENSED_MARKET,
        )
        await retention_service.purge_provider(
            db_session,
            scene["store"],
            provider=Provider.EODHD,
            reason="Subscription terminated.",
            actor=scene["operator"],
        )

        again = await retention_service.purge_provider(
            db_session,
            scene["store"],
            provider=Provider.EODHD,
            reason="Confirming the sweep.",
            actor=scene["operator"],
        )

        assert again.purged == 0

    async def test_a_purge_row_exists_for_every_artefact_swept(self, db_session, scene):
        for index in range(2):
            await store_document(
                db_session,
                scene,
                payload=PRICES + str(index).encode(),
                provider=Provider.EODHD,
                tier=SourceTier.T4_LICENSED_MARKET,
            )

        await retention_service.purge_provider(
            db_session,
            scene["store"],
            provider=Provider.EODHD,
            reason="Subscription terminated.",
            actor="retention-sweep",
        )

        rows = list(await db_session.scalars(select(ArtefactPurge)))
        assert len(rows) == 2
        assert {row.actor for row in rows} == {"retention-sweep"}
        assert all(row.actor_user_id is None for row in rows)


# -- The capability, not the convention ------------------------------------------------------


class TestErasureIsACapability:
    """The ordinary storage interface has no delete, and that is enforced by the type."""

    def test_the_read_write_protocol_offers_no_erasure(self):
        assert not hasattr(ArtefactStore, "purge")
        for forbidden in ("delete", "remove", "update", "move"):
            assert not hasattr(ArtefactStore, forbidden)

    def test_the_local_store_satisfies_both_protocols(self, artefact_store):
        """One object, two interfaces. What a caller is *given* decides what it can do."""
        assert isinstance(artefact_store, ArtefactStore)
        assert isinstance(artefact_store, PurgeableStore)

    def test_the_purge_service_asks_for_the_narrow_one(self):
        """A caller wired with the ordinary store cannot reach this module's operation."""
        # A string under `from __future__ import annotations`, which is what the module
        # under test also uses. The name is the assertion.
        annotation = inspect.signature(retention_service.purge_artefact).parameters["store"]
        assert annotation.annotation == "PurgeableStore"
