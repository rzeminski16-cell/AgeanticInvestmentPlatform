"""Getting rid of a request: the reversible way, and the one that is not.

Gap B1. Until now the only removal was `delete_request`, which refuses anything a run left
evidence or a report behind — correctly, since those are the two things that exist nowhere
else. The consequence was that a finished run stayed on the list for ever, and the list had
no per-row control at all.

Two verbs answer it, and almost every test here is about the distance between them.
Archiving destroys nothing and therefore refuses nothing. Purging destroys the request and
everything derived from it, and keeps exactly three things: the audit chain, the spend
ledger, and the content-addressed artefacts. Each of those three has its own test, because
each is a different way for a deletion to quietly become a lie.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.core.enums import ClaimKind, FactBasis, JobStatus
from aer.db.base import Base
from aer.db.models import (
    Artefact,
    AuditEvent,
    Calculation,
    Citation,
    Claim,
    Company,
    Cost,
    Extraction,
    FinancialFact,
    Job,
    ReportSection,
    ResearchRequest,
    SectionDefinition,
    SectionStatus,
    SourceDocument,
    User,
)
from aer.errors import ConflictError
from aer.services import requests as request_service
from aer.storage.local import LocalArtefactStore
from tests.scene_fixtures import build_scene

pytestmark = pytest.mark.integration


@pytest.fixture
async def scene(db_session: AsyncSession, tmp_path: Any) -> dict[str, Any]:
    """A fully researched request: a run, a filing, an excerpt, a fact, a claim, a cost.

    Built out to the point where every kind of row a purge has to reason about exists —
    including the two that must survive it.
    """
    store = LocalArtefactStore(tmp_path / "artefacts", max_bytes=1_048_576)
    base = await build_scene(db_session, store)

    company = Company(name="MICROSOFT CORP", cik="0000789019", ticker="MSFT", exchange="NASDAQ")
    db_session.add(company)
    await db_session.flush()

    fact = FinancialFact(
        company_id=company.id,
        source_document_id=base["document"].id,
        concept="revenue",
        value=Decimal("198270000000"),
        unit="USD",
        period_end=date(2022, 6, 30),
        basis=FactBasis.AS_REPORTED,
        filed_date=date(2022, 7, 28),
    )
    calculation = Calculation(
        job_id=base["job"].id,
        name="revenue_growth",
        formula="(a - b) / b",
        function_ref="aer.calc.basic:growth_rate",
        inputs=[],
        output_value=Decimal("0.18"),
        output_unit="ratio",
        code_version="test",
    )
    db_session.add_all([fact, calculation])
    await db_session.flush()

    claim = Claim(
        report_section_id=base["section"].id,
        kind=ClaimKind.NUMERIC,
        text="Revenue was $198,270 million in fiscal 2022.",
        financial_fact_id=fact.id,
    )
    db_session.add(claim)
    await db_session.flush()

    db_session.add(
        Citation(
            claim_id=claim.id,
            source_document_id=base["document"].id,
            extraction_id=base["extraction"].id,
        )
    )
    # The ledger. Nulled to its job on delete since migration 0009, and the reason a purge
    # cannot be used to get under the monthly cap.
    db_session.add(
        Cost(
            job_id=base["job"].id,
            category="model",
            provider="anthropic",
            model="claude-opus-5",
            units=Decimal("1500"),
            unit_type="tokens",
            amount_usd=Decimal("0.5250"),
            amount_gbp=Decimal("0.4200"),
            fx_rate=Decimal("0.8000"),
        )
    )
    base["job"].status = JobStatus.SUCCEEDED
    base["job"].finished_at = datetime.now(UTC)
    await db_session.flush()

    base["company"] = company
    base["fact"] = fact
    base["claim"] = claim
    base["calculation"] = calculation
    return base


async def another_request(session: AsyncSession, owner: User, *, ticker: str) -> ResearchRequest:
    other = ResearchRequest(
        user_id=owner.id,
        company_name=f"{ticker} Corporation",
        ticker=ticker,
        exchange="NYSE",
        as_of_date=date(2022, 6, 30),
        base_currency="USD",
        investment_horizon_months=12,
        max_cost_gbp="2.50",
        portfolio_context={},
    )
    session.add(other)
    await session.flush()
    return other


async def a_later_run_citing(session: AsyncSession, scene: dict[str, Any]) -> ResearchRequest:
    """A second request whose report cites a fact the first request's document supplied.

    Exactly the shape a re-run of the same company leaves behind: facts are deduplicated on
    a key that excludes the source document, so the later run stores none of its own and
    reads the earlier run's.
    """
    later = await another_request(session, scene["user"], ticker="MSFT2")
    job = Job(
        request_id=later.id,
        workflow_version="vertical_slice_v1",
        code_version="test",
        status=JobStatus.SUCCEEDED,
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
    )
    session.add(job)
    await session.flush()

    definition = await session.scalar(select(SectionDefinition).limit(1))
    assert definition is not None
    section = ReportSection(
        job_id=job.id,
        section_definition_id=definition.id,
        section_key=definition.key,
        position=definition.position,
        status=SectionStatus.GENERATED,
        content={"body": "The later run."},
    )
    session.add(section)
    await session.flush()

    session.add(
        Claim(
            report_section_id=section.id,
            kind=ClaimKind.NUMERIC,
            text="The later run cites the earlier run's fact.",
            financial_fact_id=scene["fact"].id,
        )
    )
    await session.flush()
    return later


async def count_of(session: AsyncSession, model: Any) -> int:
    """How many rows of ``model`` exist at all.

    Only ever used where the scene owns every row of that table inside its transaction.
    **Never for `artefacts` or `companies`**: both are content-addressed or shared, both
    are deliberately left behind by fixtures that truncate requests, and a test asserting
    "there is exactly one artefact in the world" fails the day some other test commits one
    — which is precisely how this file first went red in a full-suite run and passed alone.
    Use :func:`still_there` for those.
    """
    return int(await session.scalar(select(func.count()).select_from(model)) or 0)


async def still_there(session: AsyncSession, model: Any, row_id: Any) -> bool:
    """Whether one specific row survived. Order-independent, unlike a table count."""
    return await session.get(model, row_id) is not None


# -- Archiving: the one that destroys nothing -------------------------------------------------


class TestArchiving:
    async def test_a_researched_request_can_be_archived(self, db_session, scene):
        """The case `delete_request` refuses, and the reason archiving exists. A finished
        run is the *usual* thing to want off the list."""
        archived = await request_service.archive_request(
            db_session, request=scene["request"], actor=scene["user"]
        )

        assert archived.is_archived is True
        assert archived.archived_at is not None

    async def test_nothing_is_removed(self, db_session, scene):
        await request_service.archive_request(
            db_session, request=scene["request"], actor=scene["user"]
        )

        assert await count_of(db_session, SourceDocument) == 1
        assert await count_of(db_session, FinancialFact) == 1
        assert await count_of(db_session, Claim) == 1
        assert await count_of(db_session, Job) == 1

    async def test_the_status_is_left_alone(self, db_session, scene):
        """Archiving is orthogonal to where a request sits in the research lifecycle, so
        restoring does not have to guess what the status used to be."""
        before = scene["request"].status

        await request_service.archive_request(
            db_session, request=scene["request"], actor=scene["user"]
        )

        assert scene["request"].status is before

    async def test_restoring_puts_it_back(self, db_session, scene):
        await request_service.archive_request(
            db_session, request=scene["request"], actor=scene["user"]
        )
        restored = await request_service.restore_request(
            db_session, request=scene["request"], actor=scene["user"]
        )

        assert restored.archived_at is None

    async def test_archiving_twice_is_refused(self, db_session, scene):
        """A second archive is not a second event, and recording it as one would put a
        false date on the first."""
        await request_service.archive_request(
            db_session, request=scene["request"], actor=scene["user"]
        )

        with pytest.raises(ConflictError, match="was archived on"):
            await request_service.archive_request(
                db_session, request=scene["request"], actor=scene["user"]
            )

    async def test_restoring_what_was_never_archived_is_refused(self, db_session, scene):
        with pytest.raises(ConflictError, match="not archived"):
            await request_service.restore_request(
                db_session, request=scene["request"], actor=scene["user"]
            )

    @pytest.mark.parametrize(
        ("action", "event_type"),
        [("archive_request", "request.archived"), ("restore_request", "request.restored")],
    )
    async def test_both_directions_are_recorded(self, db_session, scene, action, event_type):
        if action == "restore_request":
            await request_service.archive_request(
                db_session, request=scene["request"], actor=scene["user"]
            )

        await getattr(request_service, action)(
            db_session, request=scene["request"], actor=scene["user"]
        )

        found = await db_session.scalar(
            select(AuditEvent)
            .where(AuditEvent.event_type == event_type)
            .order_by(AuditEvent.id.desc())
            .limit(1)
        )
        assert found is not None
        assert found.payload["request_id"] == str(scene["request"].id)


class TestTheTwoListsAreSeparate:
    async def test_an_archived_request_leaves_the_live_list(self, db_session, scene):
        await request_service.archive_request(
            db_session, request=scene["request"], actor=scene["user"]
        )

        live = await request_service.list_requests(db_session, user_id=scene["user"].id)

        assert [row.id for row in live] == []

    async def test_the_archive_view_shows_only_the_archived(self, db_session, scene):
        kept = await another_request(db_session, scene["user"], ticker="AAPL")
        await request_service.archive_request(
            db_session, request=scene["request"], actor=scene["user"]
        )

        archived = await request_service.list_requests(
            db_session, user_id=scene["user"].id, archived=True
        )
        live = await request_service.list_requests(db_session, user_id=scene["user"].id)

        assert [row.id for row in archived] == [scene["request"].id]
        assert [row.id for row in live] == [kept.id]

    async def test_the_counts_match_the_lists(self, db_session, scene):
        await another_request(db_session, scene["user"], ticker="AAPL")
        await request_service.archive_request(
            db_session, request=scene["request"], actor=scene["user"]
        )

        assert await request_service.count_requests(db_session, user_id=scene["user"].id) == 1
        assert (
            await request_service.count_requests(
                db_session, user_id=scene["user"].id, archived=True
            )
            == 1
        )


# -- Purging: the one that does not come back -------------------------------------------------


class TestWhatAPurgeRemoves:
    async def test_the_request_and_its_research_go(self, db_session, scene):
        await request_service.purge_request(
            db_session, request=scene["request"], actor=scene["user"]
        )

        assert await count_of(db_session, ResearchRequest) == 0
        assert await count_of(db_session, Job) == 0
        assert await count_of(db_session, SourceDocument) == 0
        assert await count_of(db_session, Extraction) == 0
        assert await count_of(db_session, FinancialFact) == 0
        assert await count_of(db_session, Claim) == 0
        assert await count_of(db_session, Citation) == 0
        assert await count_of(db_session, Calculation) == 0

    async def test_the_preview_says_what_will_go_before_it_goes(self, db_session, scene):
        """A destructive confirmation that says only "are you sure?" is asking somebody to
        agree to a number nobody has shown them."""
        preview = await request_service.removal_preview(db_session, request=scene["request"])

        removed = await request_service.purge_request(
            db_session, request=scene["request"], actor=scene["user"]
        )

        assert preview == removed
        assert preview["source_documents"] == 1
        assert preview["financial_facts"] == 1
        assert preview["citations"] == 1

    async def test_a_draft_with_no_research_purges_cleanly(self, db_session, scene):
        empty = await another_request(db_session, scene["user"], ticker="AAPL")

        removed = await request_service.purge_request(
            db_session, request=empty, actor=scene["user"]
        )

        assert removed == {}
        assert await count_of(db_session, ResearchRequest) == 1


class TestWhatAPurgeKeeps:
    """Three survivals, and each is a different way a deletion could quietly become a lie."""

    async def test_the_spend_survives_with_its_reference_nulled(self, db_session, scene):
        """A monthly cap you can get under by deleting what you spent it on is not a cap.
        `costs` references its job with SET NULL for exactly this reason, and the purge's
        ownership walk excludes any table it can only reach through a nulled edge."""
        await request_service.purge_request(
            db_session, request=scene["request"], actor=scene["user"]
        )

        rows = list(await db_session.scalars(select(Cost)))
        assert len(rows) == 1
        assert rows[0].amount_gbp == Decimal("0.4200")
        assert rows[0].job_id is None

    async def test_the_audit_chain_survives_and_records_what_went(self, db_session, scene):
        """`audit_events` carries `request_id` as a plain column with no foreign key,
        precisely so the record of a deletion outlives the thing deleted."""
        request_id = scene["request"].id

        await request_service.purge_request(
            db_session, request=scene["request"], actor=scene["user"]
        )

        event = await db_session.scalar(
            select(AuditEvent)
            .where(AuditEvent.event_type == "request.purged")
            .order_by(AuditEvent.id.desc())
            .limit(1)
        )
        assert event is not None
        assert event.payload["request_id"] == str(request_id)
        assert event.payload["ticker"] == "MSFT"
        assert event.payload["spend_gbp"] == "0.420000"
        assert event.payload["removed"]["source_documents"] == 1

    async def test_the_artefacts_survive(self, db_session, scene):
        """Content-addressed and shared between runs, so they are never one request's to
        destroy. `aer gc-artefacts` is what collects the ones nothing points at."""
        await request_service.purge_request(
            db_session, request=scene["request"], actor=scene["user"]
        )

        assert await still_there(db_session, Artefact, scene["artefact"].id)
        assert await scene["store"].exists(scene["artefact"].sha256) is True

    async def test_the_company_survives(self, db_session, scene):
        """A company is not a request's to own. The next run resolves the same one."""
        await request_service.purge_request(
            db_session, request=scene["request"], actor=scene["user"]
        )

        assert await still_there(db_session, Company, scene["company"].id)

    async def test_another_request_is_untouched(self, db_session, scene):
        kept = await another_request(db_session, scene["user"], ticker="AAPL")
        kept_job = Job(
            request_id=kept.id,
            workflow_version="vertical_slice_v1",
            code_version="test",
            status=JobStatus.SUCCEEDED,
            started_at=datetime.now(UTC),
        )
        db_session.add(kept_job)
        await db_session.flush()

        await request_service.purge_request(
            db_session, request=scene["request"], actor=scene["user"]
        )

        assert await db_session.get(ResearchRequest, kept.id) is not None
        assert await db_session.get(Job, kept_job.id) is not None

    async def test_the_user_survives(self, db_session, scene):
        await request_service.purge_request(
            db_session, request=scene["request"], actor=scene["user"]
        )

        assert await still_there(db_session, User, scene["user"].id)


class TestThePurgeRefuses:
    async def test_a_live_run_stops_it(self, db_session, scene):
        """Deleting rows a worker is writing to is a crash in the worker and a half-deleted
        request here."""
        scene["job"].status = JobStatus.RUNNING
        scene["job"].finished_at = None
        await db_session.flush()

        with pytest.raises(ConflictError, match="Cancel it first"):
            await request_service.purge_request(
                db_session, request=scene["request"], actor=scene["user"]
            )

    async def test_nothing_is_removed_by_a_refusal(self, db_session, scene):
        scene["job"].status = JobStatus.RUNNING
        scene["job"].finished_at = None
        await db_session.flush()

        with pytest.raises(ConflictError):
            await request_service.purge_request(
                db_session, request=scene["request"], actor=scene["user"]
            )

        assert await count_of(db_session, SourceDocument) == 1
        assert await count_of(db_session, FinancialFact) == 1

    async def test_a_second_run_reading_these_facts_stops_it(self, db_session, scene):
        """**The case that makes archiving the default.**

        Facts are deduplicated on a key that excludes the source document, so the second
        run of a company stores none of its own and cites the first run's — through the
        first run's document, pinned by a RESTRICT. Purging the first request would take
        facts a surviving report rests on. The database would refuse anyway; this refuses
        first, with a sentence, rather than as a foreign-key violation from inside a
        half-finished transaction.
        """
        await a_later_run_citing(db_session, scene)

        with pytest.raises(ConflictError, match="claims"):
            await request_service.purge_request(
                db_session, request=scene["request"], actor=scene["user"]
            )

    async def test_the_refusal_says_what_to_do_instead(self, db_session, scene):
        await a_later_run_citing(db_session, scene)

        with pytest.raises(ConflictError, match="archive this one instead"):
            await request_service.purge_request(
                db_session, request=scene["request"], actor=scene["user"]
            )


# -- The scoping is derived from the schema, not written down ---------------------------------


class TestOwnershipComesFromTheForeignKeys:
    """A hand-written list of what to delete is how a "removed" request keeps half its rows.

    The walk follows `CASCADE` and nothing else, because that is the schema saying "this
    row is part of that one". The other two actions say something else entirely, and
    reading either as ownership is a bug:

    * `SET NULL` says the row outlives its referent — four tables depend on that reading.
    * `RESTRICT` says "remove me first", which constrains the order of a deletion and not
      who owns what. Following it *was* this walk's first version, and it silently swept a
      later request's claim into the scope because the claim cited this request's fact.
    """

    @staticmethod
    def _names() -> list[str]:
        return [name for name, _ in request_service._owned_scopes(uuid.uuid4())]

    @pytest.mark.parametrize(
        ("table", "why"),
        [
            ("costs", "the spend ledger, nulled to its job on purpose since migration 0009"),
            ("price_bars", "per security, shared by every request that looked at it"),
            ("corporate_actions", "per security, likewise"),
            ("macro_observations", "a macro vintage belongs to nobody's run"),
        ],
    )
    def test_a_table_reachable_only_through_a_nulled_edge_is_out_of_reach(self, table, why):
        assert table in Base.metadata.tables, f"{table} should exist: {why}"
        assert table not in self._names(), why

    @pytest.mark.parametrize(
        "table",
        ["jobs", "source_documents", "financial_facts", "claims", "citations", "reports"],
    )
    def test_what_a_run_produced_is_owned(self, table):
        assert table in self._names()

    def test_nothing_outside_a_run_is_owned(self):
        owned = self._names()

        for survivor in ("users", "companies", "artefacts", "audit_events", "securities"):
            assert survivor not in owned

    def test_the_order_is_the_dependency_sort_reversed(self):
        """Deepest first, so a RESTRICT reference is never asked to break. This is the same
        order `reset-research` uses, and agreeing with it is the assertion."""
        from aer.cli import _research_tables  # noqa: PLC0415 -- CLI import cost, one test

        owned = self._names()
        reset = [name for name in _research_tables() if name in owned]

        assert reset == owned

    def test_a_child_is_always_emptied_before_its_parent(self):
        """Read off the metadata rather than listed, so a table added next month is
        covered without anybody remembering this test exists."""
        owned = self._names()
        position = {name: index for index, name in enumerate(owned)}

        for name in owned:
            for key in Base.metadata.tables[name].foreign_keys:
                parent = key.column.table.name
                if parent in position and parent != name:
                    assert position[name] < position[parent], (
                        f"{name} references {parent} and must be emptied before it"
                    )

    def test_a_restrict_reference_from_elsewhere_is_not_ownership(self):
        """The bug this rule exists to prevent, asserted structurally.

        `claims.financial_fact_id` is a nullable RESTRICT reference. Reading it as an
        ownership edge put every claim citing this request's facts into the scope —
        including a later request's, which was then deleted without anybody asking.
        """
        claims = Base.metadata.tables["claims"]
        citing = next(fk for fk in claims.foreign_keys if fk.column.table.name == "financial_facts")

        assert citing.ondelete == "RESTRICT"
        assert request_service._OWNING_EDGE == "CASCADE"


class TestOnlyOneTableIsAnException:
    """`financial_facts` is taken by policy rather than by the schema's say-so.

    A fact has no cascade path to a request — it is pinned to its source document by a
    RESTRICT — so the walk cannot find it, and leaving it behind would make the purge fail
    on that pin. That is a judgement, and a judgement made once should not quietly become a
    list of judgements nobody re-read.
    """

    def test_the_exception_is_the_table_it_says_it_is(self):
        assert request_service.FACTS_FOLLOW_THEIR_DOCUMENT == "financial_facts"
        assert "financial_facts" in [
            name for name, _ in request_service._owned_scopes(uuid.uuid4())
        ]

    def test_no_second_table_has_quietly_joined_it(self):
        """Fails the day a new table is pinned to a request-owned one by a NOT NULL
        RESTRICT and has no cascade path of its own — which is exactly the position
        `financial_facts` is in, and would need the same decision made deliberately."""
        owned = {name for name, _ in request_service._owned_scopes(uuid.uuid4())}
        cascade_reachable = owned - {request_service.FACTS_FOLLOW_THEIR_DOCUMENT}

        stranded: list[str] = []
        for table in Base.metadata.sorted_tables:
            if table.name in owned:
                continue
            pins = [
                fk
                for fk in table.foreign_keys
                if fk.ondelete == "RESTRICT"
                and not fk.parent.nullable
                and fk.column.table.name in cascade_reachable
            ]
            if pins:
                stranded.append(table.name)

        assert stranded == [], (
            f"{stranded} is pinned to a request's research and would block a purge. "
            "Decide deliberately whether it goes with the request, as financial_facts did."
        )
