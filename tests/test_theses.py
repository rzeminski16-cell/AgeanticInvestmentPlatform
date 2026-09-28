"""A thesis written down, and the rule that keeps it from becoming evidence.

Three layers. The service proves the record: a thesis with premises, each a judgement with a
holder, a time and a basis; a withdrawal that leaves the row; a retirement that closes the
door; every act on the audit chain with the thesis as its subject. The pages prove a person
can do all of that from a browser. And the structural tests prove ADR 0074's rule by walking
the metadata rather than by trusting anybody to remember it.

**The structural tests are the ones worth keeping if the rest were lost.** A judgement that
could become a `SourceRef` would pass every check this platform has — the arithmetic would
be impeccable and the figure would mean nothing — so the only defence is that the schema
cannot express it, and the only proof is a test that reads the schema.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aer.calc.units import SourceKind, SourceRef, SourceTable
from aer.core.enums import (
    Decision,
    FindingAction,
    FindingKind,
    GateKind,
    JobStatus,
    JudgementKind,
    PremiseComparator,
    PremiseStatus,
    TransactionKind,
    UserRole,
)
from aer.db.base import Base
from aer.db.models import (
    Approval,
    AuditEvent,
    Company,
    Finding,
    Job,
    Judgement,
    Portfolio,
    Premise,
    Report,
    Security,
    Thesis,
    User,
)
from aer.errors import ConflictError, ValidationError
from aer.services import theses as thesis_service
from aer.services.theses import Predicate
from aer.services.thesis_monitor import measurable_metrics
from aer.web.theses.pages import metric_groups, metric_label, threshold_words
from tests.api_fixtures import build_app, client_for
from tests.portfolio_fixtures import trade
from tests.report_fixtures import make_current
from tests.request_fixtures import research_request

pytestmark = pytest.mark.integration

REVIEW_BY = date(2027, 3, 31)


async def _user(session: AsyncSession, email: str = "holder@example.invalid") -> User:
    user = User(email=email, display_name="Holder", role=UserRole.OWNER)
    session.add(user)
    await session.flush()
    return user


async def _company(session: AsyncSession) -> Company:
    company = Company(name="Contoso plc", ticker="CTSO", exchange="LSE", company_number="01234567")
    session.add(company)
    await session.flush()
    return company


async def _thesis(
    session: AsyncSession,
    user: User,
    company: Company,
    title: str = "Contoso holds its pricing power",
) -> Thesis:
    return await thesis_service.write_thesis(session, user=user, company=company, title=title)


def _growth() -> Predicate:
    return Predicate(
        metric="revenue growth",
        comparator=PremiseComparator.AT_LEAST,
        threshold=Decimal("0.25"),
        unit="ratio",
    )


# -- The record --------------------------------------------------------------------------------


class TestAPremiseIsAJudgement:
    async def test_it_carries_a_holder_a_time_and_a_basis(self, db_session: AsyncSession) -> None:
        user = await _user(db_session)
        thesis = await _thesis(db_session, user, await _company(db_session))
        held = datetime(2026, 8, 1, tzinfo=UTC)

        premise = await thesis_service.add_premise(
            db_session,
            thesis=thesis,
            actor=user,
            statement="Azure revenue keeps growing above 25% a year.",
            basis="FY25 segment disclosure and the last four quarters' run rate.",
            predicate=_growth(),
            review_by=None,
            held_at=held,
        )

        assert premise.judgement.kind is JudgementKind.PREMISE
        assert premise.judgement.held_by == user.email
        assert premise.judgement.held_at == held
        assert premise.judgement.basis.startswith("FY25")
        assert premise.judgement.recorded_at >= held

    async def test_premises_take_their_place_in_order(self, db_session: AsyncSession) -> None:
        user = await _user(db_session)
        thesis = await _thesis(db_session, user, await _company(db_session))
        for statement in ("first", "second", "third"):
            await thesis_service.add_premise(
                db_session,
                thesis=thesis,
                actor=user,
                statement=statement,
                basis="a basis",
                predicate=None,
                review_by=REVIEW_BY,
            )

        reloaded = await thesis_service.thesis_of(db_session, thesis.id, user_id=user.id)

        assert reloaded is not None
        assert [row.position for row in reloaded.premises] == [1, 2, 3]
        assert [row.statement for row in reloaded.premises] == ["first", "second", "third"]

    async def test_a_predicate_is_stored_whole(self, db_session: AsyncSession) -> None:
        user = await _user(db_session)
        thesis = await _thesis(db_session, user, await _company(db_session))

        premise = await thesis_service.add_premise(
            db_session,
            thesis=thesis,
            actor=user,
            statement="s",
            basis="b",
            predicate=_growth(),
            review_by=None,
        )

        assert premise.has_predicate
        assert premise.metric == "revenue growth"
        assert premise.comparator is PremiseComparator.AT_LEAST
        assert premise.threshold == Decimal("0.25")
        assert premise.unit == "ratio"
        assert premise.review_by is None

    async def test_a_premise_nothing_can_test_is_somebodys_to_review(
        self, db_session: AsyncSession
    ) -> None:
        """ADR 0079's optionality, with the half that keeps it honest: no predicate means a
        date, or the platform has stored a view it will silently stop asking about."""
        user = await _user(db_session)
        thesis = await _thesis(db_session, user, await _company(db_session))

        premise = await thesis_service.add_premise(
            db_session,
            thesis=thesis,
            actor=user,
            statement="Management allocates capital well.",
            basis="Ten years of buybacks below intrinsic value.",
            predicate=None,
            review_by=REVIEW_BY,
        )

        assert not premise.has_predicate
        assert premise.review_by == REVIEW_BY

    async def test_neither_a_predicate_nor_a_review_date_is_refused(
        self, db_session: AsyncSession
    ) -> None:
        user = await _user(db_session)
        thesis = await _thesis(db_session, user, await _company(db_session))

        with pytest.raises(ValidationError, match="look at it again"):
            await thesis_service.add_premise(
                db_session,
                thesis=thesis,
                actor=user,
                statement="s",
                basis="b",
                predicate=None,
                review_by=None,
            )

    @pytest.mark.parametrize("raw", ["NaN", "Infinity", "1e40"])
    async def test_a_threshold_that_is_not_a_figure_is_refused(self, raw: str) -> None:
        """NaN and infinity store and read as "unobservable" for ever; 1e40 overflows the
        column as a database error the form cannot explain. Refused where the operator is."""
        with pytest.raises(ValidationError, match="finite figure"):
            Predicate(
                metric="revenue growth",
                comparator=PremiseComparator.AT_LEAST,
                threshold=Decimal(raw),
                unit="percent",
            )

    async def test_a_predicate_with_no_unit_is_refused(self) -> None:
        """A bare number cannot be compared with a fact — a threshold in per cent must say so,
        or it will one day be compared against a figure in dollars."""
        with pytest.raises(ValidationError, match="no unit"):
            Predicate(
                metric="revenue growth",
                comparator=PremiseComparator.AT_LEAST,
                threshold=Decimal("25"),
                unit="  ",
            )

    @pytest.mark.parametrize("field", ["statement", "basis"])
    async def test_a_blank_statement_or_basis_is_refused(
        self, db_session: AsyncSession, field: str
    ) -> None:
        user = await _user(db_session)
        thesis = await _thesis(db_session, user, await _company(db_session))
        values = {"statement": "s", "basis": "b", field: "   "}

        with pytest.raises(ValidationError):
            await thesis_service.add_premise(
                db_session,
                thesis=thesis,
                actor=user,
                predicate=None,
                review_by=REVIEW_BY,
                **values,
            )


class TestNothingIsDeleted:
    async def test_a_withdrawn_premise_stays_with_its_reason(
        self, db_session: AsyncSession
    ) -> None:
        user = await _user(db_session)
        thesis = await _thesis(db_session, user, await _company(db_session))
        premise = await thesis_service.add_premise(
            db_session,
            thesis=thesis,
            actor=user,
            statement="s",
            basis="b",
            predicate=None,
            review_by=REVIEW_BY,
        )

        await thesis_service.withdraw_premise(
            db_session, premise=premise, actor=user, reason="The FY26 margin guide broke it."
        )

        row = await db_session.get(Judgement, premise.judgement_id)
        assert row is not None
        assert row.is_withdrawn
        assert row.withdrawn_reason == "The FY26 margin guide broke it."
        assert row.basis == "b", "the view as held is untouched by the change of mind"

    async def test_a_second_withdrawal_cannot_overwrite_the_first_reason(
        self, db_session: AsyncSession
    ) -> None:
        user = await _user(db_session)
        thesis = await _thesis(db_session, user, await _company(db_session))
        premise = await thesis_service.add_premise(
            db_session,
            thesis=thesis,
            actor=user,
            statement="s",
            basis="b",
            predicate=None,
            review_by=REVIEW_BY,
        )
        await thesis_service.withdraw_premise(db_session, premise=premise, actor=user, reason="one")

        with pytest.raises(ConflictError, match="already withdrawn"):
            await thesis_service.withdraw_premise(
                db_session, premise=premise, actor=user, reason="two"
            )

    async def test_a_withdrawal_needs_a_reason(self, db_session: AsyncSession) -> None:
        user = await _user(db_session)
        thesis = await _thesis(db_session, user, await _company(db_session))
        premise = await thesis_service.add_premise(
            db_session,
            thesis=thesis,
            actor=user,
            statement="s",
            basis="b",
            predicate=None,
            review_by=REVIEW_BY,
        )

        with pytest.raises(ValidationError, match="needs a reason"):
            await thesis_service.withdraw_premise(
                db_session, premise=premise, actor=user, reason=""
            )

    async def test_a_retired_thesis_takes_no_new_premises(self, db_session: AsyncSession) -> None:
        user = await _user(db_session)
        thesis = await _thesis(db_session, user, await _company(db_session))
        await thesis_service.retire_thesis(
            db_session, thesis=thesis, actor=user, reason="Replaced by a narrower one."
        )

        assert thesis.is_retired
        with pytest.raises(ConflictError, match="retired"):
            await thesis_service.add_premise(
                db_session,
                thesis=thesis,
                actor=user,
                statement="s",
                basis="b",
                predicate=None,
                review_by=REVIEW_BY,
            )

    async def test_retired_theses_leave_the_open_list_and_are_kept(
        self, db_session: AsyncSession
    ) -> None:
        user = await _user(db_session)
        company = await _company(db_session)
        kept = await _thesis(db_session, user, company, title="kept")
        gone = await _thesis(db_session, user, company, title="gone")
        await thesis_service.retire_thesis(db_session, thesis=gone, actor=user, reason="r")

        open_titles = [
            t.title for t in await thesis_service.theses_for(db_session, user_id=user.id)
        ]
        retired_titles = [
            t.title
            for t in await thesis_service.theses_for(db_session, user_id=user.id, retired=True)
        ]

        assert open_titles == [kept.title]
        assert retired_titles == [gone.title]


class TestWhoseItIs:
    async def test_somebody_elses_thesis_answers_as_missing(self, db_session: AsyncSession) -> None:
        owner = await _user(db_session)
        stranger = await _user(db_session, "stranger@example.invalid")
        thesis = await _thesis(db_session, owner, await _company(db_session))

        assert await thesis_service.thesis_of(db_session, thesis.id, user_id=stranger.id) is None
        assert await thesis_service.thesis_of(db_session, thesis.id, user_id=owner.id) is not None

    async def test_a_thesis_outlives_its_company(self, db_session: AsyncSession) -> None:
        """The subject is a kind and an id with no foreign key (ADR 0072's shape): deleting
        a company from the registry must not delete what somebody thought of it."""
        user = await _user(db_session)
        company = await _company(db_session)
        thesis = await _thesis(db_session, user, company)

        await db_session.delete(company)
        await db_session.flush()

        reloaded = await thesis_service.thesis_of(db_session, thesis.id, user_id=user.id)
        assert reloaded is not None
        assert (
            await thesis_service.subject_name(db_session, reloaded)
            == "a company no longer on record"
        )


class TestTheAuditChainReachesIt:
    async def test_every_act_is_chained_with_the_thesis_as_its_subject(
        self, db_session: AsyncSession
    ) -> None:
        """ADR 0078: a thesis edit landing outside the chain would make the most
        consequential record in the system the least tamper-evident."""
        user = await _user(db_session)
        thesis = await _thesis(db_session, user, await _company(db_session))
        premise = await thesis_service.add_premise(
            db_session,
            thesis=thesis,
            actor=user,
            statement="s",
            basis="b",
            predicate=_growth(),
            review_by=None,
        )
        await thesis_service.withdraw_premise(db_session, premise=premise, actor=user, reason="r")
        await thesis_service.retire_thesis(db_session, thesis=thesis, actor=user, reason="done")

        events = list(
            await db_session.scalars(
                select(AuditEvent)
                .where(AuditEvent.subject_kind == "thesis", AuditEvent.subject_id == thesis.id)
                .order_by(AuditEvent.id)
            )
        )

        assert [event.event_type for event in events] == [
            "thesis.written",
            "thesis.premise_added",
            "thesis.premise_withdrawn",
            "thesis.retired",
        ]
        assert all(event.actor == user.email for event in events)
        # Chained: each carries the previous hash, and the first premise event names the
        # predicate that was written, so the record of what was believed is in the log.
        assert events[1].prev_hash == events[0].this_hash
        assert events[1].payload["predicate"]["metric"] == "revenue growth"


# -- The rule ------------------------------------------------------------------------------------


class TestAJudgementIsNeverASourceReference:
    """ADR 0074, proved off the schema rather than remembered."""

    def test_source_kind_has_no_fifth_member(self) -> None:
        assert {member.value for member in SourceKind} == {
            "fact",
            "calculation",
            "assumption",
            "attestation",
        }

    def test_source_ref_has_no_judgement_constructor(self) -> None:
        constructors = {name for name in dir(SourceRef) if not name.startswith("_")}
        assert not any("judgement" in name for name in constructors), constructors

    def test_no_source_table_names_the_judgement_tables(self) -> None:
        tables = {member.value for member in SourceTable}
        assert not tables & {"judgements", "theses", "premises"}

    def test_claims_have_no_column_for_a_judgement(self) -> None:
        columns = {column.name for column in Base.metadata.tables["claims"].columns}
        assert not any("judgement" in name or "thesis" in name for name in columns), columns

    def test_only_the_subtypes_reference_judgements(self) -> None:
        """The check that keeps the rule structural. A later table pointing at a judgement
        would be the first step of a judgement entering a lineage, and this names it. The
        three subtypes are the judgement seen from its thesis, from its consequence and
        from its outcome (ADRs 0102, 0104, 0105); none is a source."""
        referrers = sorted(
            table.name
            for table in Base.metadata.sorted_tables
            for key in table.foreign_keys
            if key.column.table.name == "judgements" and table.name != "judgements"
        )
        assert referrers == ["decisions", "premises", "reviews"]

    def test_no_column_could_hold_a_conviction(self) -> None:
        """Not a rule ADR 0074 states — it permits a stored confidence for calibration — but
        none exists yet, and one should arrive with the surface that reads it."""
        suspicious = re.compile(r"convict|confid|score|weight|probab", re.IGNORECASE)
        for name in ("judgements", "theses", "premises"):
            columns = [
                c.name for c in Base.metadata.tables[name].columns if suspicious.search(c.name)
            ]
            assert not columns, (name, columns)

    def test_the_one_number_a_premise_carries_is_not_a_quantity(self) -> None:
        """The threshold is a `Decimal` on a row, never a `Quantity` with a source. A
        `Quantity` is what `@traced` consumes; a bare column is what it refuses."""
        premise = Premise.__table__.columns["threshold"]
        assert premise.type.python_type is Decimal


# -- The pages -----------------------------------------------------------------------------------


_TABLES = (
    "audit_events, users, companies, securities, portfolios, attestations, work_orders, "
    "theses, judgements"
)


@pytest.fixture
async def scene(db_engine: Any) -> Any:
    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    async with factory() as session:
        user = User(email="owner@example.invalid", display_name="Owner", role=UserRole.OWNER)
        company = Company(
            name="Contoso plc", ticker="CTSO", exchange="LSE", company_number="01234567"
        )
        session.add_all([user, company])
        await session.commit()
        yield {"user": user, "company": company, "factory": factory}
    async with db_engine.begin() as connection:
        await connection.execute(text(f"TRUNCATE {_TABLES} RESTART IDENTITY CASCADE"))


@pytest.fixture
async def api(api_settings: Any, db_engine: Any, fake_redis: Any, scene: dict[str, Any]) -> Any:
    async for client in client_for(build_app(api_settings, engine=db_engine, redis=fake_redis)):
        yield client


def _csrf(html: str) -> str:
    found = re.search(r'name="csrf_token"\s+value="([^"]+)"', html)
    assert found is not None, "the page rendered no CSRF token"
    return found.group(1)


async def _written(api: Any, scene: dict[str, Any]) -> str:
    page = await api.get("/theses")
    response = await api.post(
        "/theses",
        data={
            "csrf_token": _csrf(page.text),
            "title": "Contoso holds its pricing power",
            "company_id": str(scene["company"].id),
            "written_on": "2026-08-01",
        },
    )
    assert response.status_code == 303, response.text
    return str(response.headers["location"])


class TestThePages:
    async def test_a_thesis_is_written_and_opened(self, api: Any, scene: dict[str, Any]) -> None:
        location = await _written(api, scene)

        opened = await api.get(location)

        assert opened.status_code == 200
        assert "Contoso holds its pricing power" in opened.text
        assert "Contoso plc (CTSO)" in opened.text
        assert "Nothing asserted yet" in opened.text
        # The date the operator gave, not the day the row appeared (ADR 0075's two clocks).
        assert 'data-field="written">1 August 2026<' in opened.text

    async def test_a_premise_of_each_kind_is_added_and_counted(
        self, api: Any, scene: dict[str, Any]
    ) -> None:
        location = await _written(api, scene)
        page = await api.get(location)
        token = _csrf(page.text)

        tested = await api.post(
            f"{location}/premises",
            data={
                "csrf_token": token,
                "statement": "Revenue keeps growing above 25% a year.",
                "basis": "The segment disclosure.",
                "held_on": "2026-08-01",
                "defeated_by": "threshold",
                "metric": "revenue growth",
                "comparator": "at_least",
                "threshold": "25",
                "unit": "percent",
            },
        )
        reviewed = await api.post(
            f"{location}/premises",
            data={
                "csrf_token": token,
                "statement": "Management allocates capital well.",
                "basis": "Ten years of buybacks below intrinsic value.",
                "defeated_by": "review",
                "review_by": (datetime.now(UTC).date() + timedelta(days=200)).isoformat(),
            },
        )
        assert tested.status_code == 303, tested.text
        assert reviewed.status_code == 303, reviewed.text

        opened = await api.get(location)
        assert 'data-field="testable">1 of 2<' in opened.text
        # The test in words, in the unit it was stated in (§10.1).
        assert "Revenue growth at least 25.0%" in opened.text
        assert 'data-tested="threshold"' in opened.text
        assert 'data-tested="review"' in opened.text

    async def test_a_premise_with_nothing_to_defeat_it_is_refused_on_the_page(
        self, api: Any, scene: dict[str, Any]
    ) -> None:
        location = await _written(api, scene)
        token = _csrf((await api.get(location)).text)

        refused = await api.post(
            f"{location}/premises",
            data={
                "csrf_token": token,
                "statement": "s",
                "basis": "b",
                "defeated_by": "review",
                "review_by": "",
            },
        )

        assert refused.status_code == 422
        assert "look at it again" in refused.text

    async def test_withdrawing_keeps_the_premise_on_the_page(
        self, api: Any, scene: dict[str, Any]
    ) -> None:
        location = await _written(api, scene)
        token = _csrf((await api.get(location)).text)
        await api.post(
            f"{location}/premises",
            data={
                "csrf_token": token,
                "statement": "Held for now.",
                "basis": "b",
                "defeated_by": "review",
                "review_by": (datetime.now(UTC).date() + timedelta(days=30)).isoformat(),
            },
        )
        page = await api.get(location)
        judgement_id = re.search(r'id="premise-([0-9a-f-]+)"', page.text)
        assert judgement_id is not None

        withdrawn = await api.post(
            f"{location}/premises/{judgement_id.group(1)}/withdraw",
            data={"csrf_token": _csrf(page.text), "reason": "The guide broke it."},
        )
        assert withdrawn.status_code == 303

        after = await api.get(location)
        assert 'data-withdrawn="yes"' in after.text
        assert "Held for now." in after.text
        assert "The guide broke it." in after.text
        assert 'id="given-up"' in after.text

    async def test_retiring_closes_the_forms(self, api: Any, scene: dict[str, Any]) -> None:
        location = await _written(api, scene)
        token = _csrf((await api.get(location)).text)

        retired = await api.post(
            f"{location}/retire",
            data={"csrf_token": token, "reason": "Replaced by a narrower one."},
        )
        assert retired.status_code == 303

        after = await api.get(location)
        assert 'id="retired-notice"' in after.text
        assert 'id="add-premise"' not in after.text
        assert 'id="retire-thesis"' not in after.text
        listing = await api.get("/theses")
        assert "Contoso holds its pricing power" not in listing.text
        assert "Contoso holds its pricing power" in (await api.get("/theses?retired=1")).text

    async def test_a_thesis_that_is_not_yours_answers_as_missing(self, api: Any) -> None:
        assert (await api.get(f"/theses/{uuid.uuid4()}")).status_code == 404

    async def test_a_missing_token_writes_nothing(self, api: Any, scene: dict[str, Any]) -> None:
        response = await api.post(
            "/theses", data={"title": "t", "company_id": str(scene["company"].id)}
        )

        assert response.status_code == 403
        assert "Nothing was written" in response.text


async def _approved_report(
    session: AsyncSession, *, user: User, company: Company, as_of: date, approved: bool = True
) -> Report:
    """A report on the company, approved unless told otherwise. The rows a report needs
    beneath it — a request and a job — carry nothing this page reads."""
    request = research_request(
        user_id=user.id,
        company_name=company.name,
        ticker=company.ticker,
        exchange=company.exchange,
        as_of_date=as_of,
        base_currency="GBP",
        reporting_currency="GBP",
        investment_horizon_months=12,
        max_cost_gbp="2.50",
    )
    session.add(request)
    await session.flush()
    job = Job(
        work_order_id=request.id,
        workflow_version="theses_scene_v1",
        code_version="thesescode1234",
        status=JobStatus.SUCCEEDED,
    )
    session.add(job)
    await session.flush()
    report = Report(
        job_id=job.id,
        request_id=request.id,
        company_id=company.id,
        as_of_date=as_of,
        content={"markdown": "approved"},
        content_hash="a" * 64,
        approved_at=datetime(2026, 7, 1, 9, tzinfo=UTC) if approved else None,
    )
    session.add(report)
    await session.flush()
    if approved:
        await make_current(session, report)
    return report


async def _book_that_dealt(
    session: AsyncSession, scene: dict[str, Any], *, sold: bool
) -> tuple[Portfolio, Security]:
    """A sterling book that bought the subject's London listing, and sold it if told to."""
    book = Portfolio(user_id=scene["user"].id, name="ISA", base_currency="GBP")
    session.add(book)
    await session.flush()
    listing = Security(
        company_id=scene["company"].id,
        ticker="CTSO",
        exchange="LSE",
        provider_symbol="CTSO.LSE",
        name="Contoso plc",
        quote_currency="GBX",
    )
    session.add(listing)
    await session.flush()
    holder = {"portfolio": book, "document": None}
    await trade(
        session,
        holder,
        kind=TransactionKind.BUY,
        security=listing,
        quantity="100",
        price="250",
        currency="GBX",
        on=date(2026, 1, 5),
    )
    if sold:
        await trade(
            session,
            holder,
            kind=TransactionKind.SELL,
            security=listing,
            quantity="-100",
            price="300",
            currency="GBX",
            on=date(2026, 3, 16),
            at_hour=16,
        )
    return book, listing


class TestWhatSurroundsAThesis:
    """The subject is a company; the research tool has reports about it and the book may
    hold it. Both reach the page as queries over the subject, never as foreign keys
    (ADR 0064) — except the one report the thesis names as what it was written against."""

    async def test_the_reports_on_the_subject_are_listed_and_the_one_written_against_marked(
        self, api: Any, scene: dict[str, Any]
    ) -> None:
        async with scene["factory"]() as session:
            read = await _approved_report(
                session, user=scene["user"], company=scene["company"], as_of=date(2026, 6, 30)
            )
            older = await _approved_report(
                session, user=scene["user"], company=scene["company"], as_of=date(2025, 6, 30)
            )
            draft = await _approved_report(
                session,
                user=scene["user"],
                company=scene["company"],
                as_of=date(2026, 9, 30),
                approved=False,
            )
            await session.commit()

        form = await api.get("/theses")
        assert f'<option value="{read.id}"' in form.text
        assert f'<option value="{draft.id}"' not in form.text, "a draft is not a report yet"

        response = await api.post(
            "/theses",
            data={
                "csrf_token": _csrf(form.text),
                "title": "Contoso holds its pricing power",
                "company_id": str(scene["company"].id),
                "report_id": str(read.id),
                "written_on": "2026-08-01",
            },
        )
        assert response.status_code == 303, response.text
        opened = await api.get(str(response.headers["location"]))

        assert f'href="/reports/{read.id}"' in opened.text
        assert f'href="/reports/{older.id}"' in opened.text
        assert f'href="/reports/{draft.id}"' not in opened.text
        assert "Report as of 30 June 2026" in opened.text
        assert opened.text.count("The report this thesis was written against.") == 1
        assert 'data-field="written-against"' in opened.text

    async def test_a_report_about_another_company_is_refused(
        self, api: Any, scene: dict[str, Any]
    ) -> None:
        async with scene["factory"]() as session:
            other = Company(
                name="Fabrikam plc", ticker="FBRK", exchange="LSE", company_number="07654321"
            )
            session.add(other)
            await session.flush()
            elsewhere = await _approved_report(
                session, user=scene["user"], company=other, as_of=date(2026, 6, 30)
            )
            await session.commit()

        form = await api.get("/theses")
        refused = await api.post(
            "/theses",
            data={
                "csrf_token": _csrf(form.text),
                "title": "Contoso holds its pricing power",
                "company_id": str(scene["company"].id),
                "report_id": str(elsewhere.id),
            },
        )

        assert refused.status_code == 422
        assert "about a different company" in refused.text
        async with scene["factory"]() as session:
            assert (await session.scalar(select(Thesis))) is None

    async def test_with_no_report_the_page_points_at_research(
        self, api: Any, scene: dict[str, Any]
    ) -> None:
        opened = await api.get(await _written(api, scene))

        assert "No report yet" in opened.text
        assert 'href="/requests/new"' in opened.text

    async def test_an_open_position_links_to_the_book(
        self, api: Any, scene: dict[str, Any]
    ) -> None:
        async with scene["factory"]() as session:
            await _book_that_dealt(session, scene, sold=False)
            await session.commit()

        opened = await api.get(await _written(api, scene))

        assert "CTSO on LSE, open in ISA" in opened.text
        assert "Opened 05 January 2026." in opened.text
        assert "1 trade on record" in opened.text
        assert 'href="/portfolio"' in opened.text

    async def test_a_closed_position_links_to_its_review(
        self, api: Any, scene: dict[str, Any]
    ) -> None:
        async with scene["factory"]() as session:
            await _book_that_dealt(session, scene, sold=True)
            await session.commit()

        opened = await api.get(await _written(api, scene))

        assert "CTSO on LSE, closed 16 March 2026" in opened.text
        assert "Held from 05 January 2026 to 16 March 2026 in ISA. Not yet reviewed." in opened.text
        assert "2 trades on record" in opened.text
        assert 'href="/review"' in opened.text
        assert "open in ISA" not in opened.text

    async def test_a_book_that_never_dealt_in_the_subject_says_so(
        self, api: Any, scene: dict[str, Any]
    ) -> None:
        async with scene["factory"]() as session:
            session.add(Portfolio(user_id=scene["user"].id, name="ISA", base_currency="GBP"))
            await session.commit()

        opened = await api.get(await _written(api, scene))

        assert "ISA has never dealt in Contoso plc (CTSO)" in opened.text
        assert 'href="/decisions/new?thesis=' in opened.text

    async def test_with_no_book_the_position_says_so(self, api: Any, scene: dict[str, Any]) -> None:
        opened = await api.get(await _written(api, scene))

        assert "No book yet" in opened.text
        assert 'href="/portfolio"' in opened.text


class TestTheAddForm:
    async def test_the_choice_leads_to_its_fields(self, api: Any, scene: dict[str, Any]) -> None:
        """The script hides the branch the radio did not choose; the markup declares which
        branch is which, and both are rendered so a browser without scripting sees the
        form as it always was."""
        opened = await api.get(await _written(api, scene))

        assert 'data-branches="defeated_by"' in opened.text
        assert 'id="threshold-fields" data-branch="threshold"' in opened.text
        assert 'id="review-fields" data-branch="review"' in opened.text
        assert "/js/branches.js" in opened.text

    async def test_the_metric_offers_only_what_the_monitor_measures(
        self, api: Any, scene: dict[str, Any]
    ) -> None:
        """The monitor resolves a metric by name (ADR 0103); a name it does not know is read
        as unobservable, which is late. So the field is a select of the names it resolves,
        in words (§10's *must not*: offer a metric the monitor cannot resolve)."""
        opened = await api.get(await _written(api, scene))

        assert '<select id="metric" name="metric"' in opened.text
        for metric in measurable_metrics():
            assert f'<option value="{metric}">{metric_label(metric)}</option>' in opened.text


class TestTheEmptyStates:
    async def test_with_no_companies_the_form_points_at_research(
        self, api_settings: Any, db_engine: Any, fake_redis: Any
    ) -> None:
        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as session:
            session.add(User(email="owner@example.invalid", display_name="O", role=UserRole.OWNER))
            await session.commit()
        try:
            async for client in client_for(
                build_app(api_settings, engine=db_engine, redis=fake_redis)
            ):
                page = await client.get("/theses")
                assert page.status_code == 200
                assert "Nothing to be about yet" in page.text
                assert 'href="/requests/new"' in page.text
                assert 'id="write-thesis"' not in page.text
        finally:
            async with db_engine.begin() as connection:
                await connection.execute(text(f"TRUNCATE {_TABLES} RESTART IDENTITY CASCADE"))


# -- The editor (page specification §10) -----------------------------------------------------------


class TestARevisionIsTwoRows:
    """§10.3: nothing deletes a premise. A revision withdraws the old wording with the reason
    and writes the new one as a judgement that supersedes it, so the history can be read as
    *on 3 March you believed…, on 14 September you revised it to…*."""

    async def test_the_old_wording_is_kept_and_the_new_one_supersedes_it(
        self, db_session: AsyncSession
    ) -> None:
        user = await _user(db_session)
        thesis = await _thesis(db_session, user, await _company(db_session))
        old = await thesis_service.add_premise(
            db_session,
            thesis=thesis,
            actor=user,
            statement="Revenue grows above 25% a year.",
            basis="The segment disclosure.",
            predicate=_growth(),
            review_by=None,
        )

        new = await thesis_service.revise_premise(
            db_session,
            thesis=thesis,
            premise=old,
            actor=user,
            statement="Revenue grows above 15% a year.",
            predicate=Predicate(
                metric="revenue growth",
                comparator=PremiseComparator.AT_LEAST,
                threshold=Decimal(15),
                unit="percent",
            ),
            review_by=None,
            reason="The FY26 guide cut the growth rate.",
        )

        assert old.judgement.withdrawn_reason == "The FY26 guide cut the growth rate."
        assert old.statement == "Revenue grows above 25% a year.", "the old row is untouched"
        assert new.judgement.supersedes_id == old.judgement_id
        assert new.judgement.basis == "The segment disclosure.", "the grounds carry over"
        assert new.threshold == Decimal(15)
        assert new.position > old.position
        revised = await db_session.scalar(
            select(AuditEvent).where(AuditEvent.event_type == "thesis.premise_revised")
        )
        assert revised is not None
        assert revised.payload["supersedes_id"] == str(old.judgement_id)
        assert revised.subject_id == thesis.id

    async def test_a_revision_needs_a_reason(self, db_session: AsyncSession) -> None:
        user = await _user(db_session)
        thesis = await _thesis(db_session, user, await _company(db_session))
        premise = await thesis_service.add_premise(
            db_session,
            thesis=thesis,
            actor=user,
            statement="s",
            basis="b",
            predicate=None,
            review_by=REVIEW_BY,
        )

        with pytest.raises(ValidationError, match="needs a reason"):
            await thesis_service.revise_premise(
                db_session,
                thesis=thesis,
                premise=premise,
                actor=user,
                statement="t",
                predicate=None,
                review_by=REVIEW_BY,
                reason="  ",
            )
        assert not premise.judgement.is_withdrawn

    async def test_a_premise_already_given_up_cannot_be_revised(
        self, db_session: AsyncSession
    ) -> None:
        """A second revision of one row would fork its history; the unique supersession
        would refuse it too, but the service says why first."""
        user = await _user(db_session)
        thesis = await _thesis(db_session, user, await _company(db_session))
        premise = await thesis_service.add_premise(
            db_session,
            thesis=thesis,
            actor=user,
            statement="s",
            basis="b",
            predicate=None,
            review_by=REVIEW_BY,
        )
        await thesis_service.withdraw_premise(
            db_session, premise=premise, actor=user, reason="Gone."
        )

        with pytest.raises(ConflictError, match="already withdrawn or revised"):
            await thesis_service.revise_premise(
                db_session,
                thesis=thesis,
                premise=premise,
                actor=user,
                statement="t",
                predicate=None,
                review_by=REVIEW_BY,
                reason="r",
            )


class TestAPremiseInWords:
    """Thresholds and metrics as a person reads them — the §10.1 test controls and every
    summary line show *Revenue growth at least 8.0%*, never `revenue_growth 8 percent`."""

    def test_a_metric_is_named_in_words(self) -> None:
        assert metric_label("revenue_growth") == "Revenue growth"
        assert metric_label("revenue growth") == "Revenue growth"
        assert metric_label("gross_margin") == "Gross margin"
        assert metric_label("net_debt_to_ebitda") == "Net debt to EBITDA"
        assert metric_label("customer churn") == "customer churn", "the operator's own words"

    def test_a_threshold_is_shown_in_the_unit_it_was_stated_in(self) -> None:
        assert threshold_words(Decimal("8.000000000000"), "percent") == "8.0%"
        assert threshold_words(Decimal(2), "ratio") == "2\N{MULTIPLICATION SIGN}"
        assert threshold_words(Decimal(45), "day") == "45 days"
        assert threshold_words(Decimal("50000000"), "USD") == "50,000,000 USD"

    def test_the_select_offers_exactly_what_the_monitor_can_measure(self) -> None:
        """§10's *must not*: offer a metric the monitor cannot resolve."""
        offered = [choice["value"] for group in metric_groups() for choice in group["choices"]]

        assert sorted(offered) == sorted(measurable_metrics())
        labels = [choice["label"] for group in metric_groups() for choice in group["choices"]]
        assert not any("_" in label for label in labels)


def _observed(value: str, *, holds: bool) -> dict[str, Any]:
    """A reading's measurement, in the shape `thesis_monitor.Measurement.as_json` writes."""
    return {
        "metric": "revenue growth",
        "value": value,
        "unit": "ratio",
        "period_end": "2025-12-31",
        "prior_value": "",
        "prior_period_end": "",
        "threshold": "0.08",
        "comparator": "at least",
        "holds": holds,
        "calculation_id": None,
        "fact_id": None,
        "threshold_unit": "ratio",
    }


async def _a_pass(session: AsyncSession, scene: dict[str, Any]) -> Job:
    """A run root a gate's approval can hang off (ADR 0072). Any work order carries it."""
    request = research_request(
        user_id=scene["user"].id,
        company_name="Contoso plc",
        ticker="CTSO",
        exchange="LSE",
        as_of_date=date(2026, 9, 11),
        base_currency="GBP",
        reporting_currency="GBP",
        investment_horizon_months=12,
        max_cost_gbp="2.50",
    )
    session.add(request)
    await session.flush()
    job = Job(
        work_order_id=request.id,
        workflow_version="thesis_monitor_v1",
        code_version="thesescode1234",
        status=JobStatus.SUCCEEDED,
    )
    session.add(job)
    await session.flush()
    return job


async def _editor_scene(scene: dict[str, Any], *, gate: bool = False) -> dict[str, Any]:
    """A thesis the monitor has read: one premise broke, one holds, one not yet read, and
    one a person reviews by a date."""
    async with scene["factory"]() as session:
        user = await session.get(User, scene["user"].id)
        company = await session.get(Company, scene["company"].id)
        assert user is not None
        assert company is not None
        thesis = await _thesis(session, user, company)

        async def premise(statement: str, predicate: Predicate | None) -> Premise:
            return await thesis_service.add_premise(
                session,
                thesis=thesis,
                actor=user,
                statement=statement,
                basis="The annual report.",
                predicate=predicate,
                review_by=None if predicate else REVIEW_BY,
            )

        broke = await premise(
            "The pipeline replaces the lost revenue.",
            Predicate("revenue_growth", PremiseComparator.AT_LEAST, Decimal(8), "percent"),
        )
        holds = await premise(
            "Gross margin holds.",
            Predicate("gross_margin", PremiseComparator.AT_LEAST, Decimal(78), "percent"),
        )
        unread = await premise(
            "Leverage stays low.",
            Predicate("net_debt_to_ebitda", PremiseComparator.AT_MOST, Decimal(2), "ratio"),
        )
        await premise("Management allocates capital well.", None)
        job = await _a_pass(session, scene) if gate else None
        read = datetime(2026, 9, 11, 7, tzinfo=UTC)
        broken = Finding(
            user_id=user.id,
            thesis_id=thesis.id,
            judgement_id=broke.judgement_id,
            job_id=job.id if job is not None else None,
            kind=FindingKind.READING,
            status=PremiseStatus.CONTRADICTED,
            justification="Growth slowed.",
            source_document_ids=[],
            observed=_observed("0.041", holds=False),
            window_from=date(2026, 3, 4),
            window_to=date(2026, 9, 10),
            opens_gate=True,
            created_at=read,
        )
        held = Finding(
            user_id=user.id,
            thesis_id=thesis.id,
            judgement_id=holds.judgement_id,
            job_id=None,
            kind=FindingKind.READING,
            status=PremiseStatus.UNCHANGED,
            justification="Margin held.",
            source_document_ids=[],
            observed=_observed("0.819", holds=True),
            window_from=date(2026, 3, 4),
            window_to=date(2026, 9, 10),
            opens_gate=False,
            created_at=read,
        )
        session.add_all([broken, held])
        await session.commit()
        return {
            "thesis": thesis,
            "broke": broke,
            "holds": holds,
            "unread": unread,
            "finding": broken,
            "location": f"/theses/{thesis.id}",
        }


def _row_of(html: str, judgement_id: uuid.UUID) -> str:
    """One premise's row of the editor, for assertions that must not match its neighbours.

    Up to the next premise's row rather than the first ``</li>``: a row's history is a list
    of its own.
    """
    start = html.index(f'id="premise-{judgement_id}"')
    following = html.find('<li id="premise-', start)
    return html[start : following if following != -1 else html.find("</form>", start)]


class TestTheEditor:
    async def test_each_premise_says_what_the_monitor_last_read(
        self, api: Any, scene: dict[str, Any]
    ) -> None:
        """§10.1: *holds*, *broke*, *by hand* — and *not read yet* for a test nothing has read,
        because none of the three is true of it."""
        editor = await _editor_scene(scene)

        page = (await api.get(editor["location"])).text

        broke = _row_of(page, editor["broke"].judgement_id)
        assert 'data-state="broke"' in broke
        assert "Measured 4.1% for the year to 31 December 2025, read on 11 September 2026." in broke
        held = _row_of(page, editor["holds"].judgement_id)
        assert 'data-state="holds"' in held
        assert "Measured 81.9%" in held
        assert 'data-state="not read yet"' in _row_of(page, editor["unread"].judgement_id)
        assert page.count('data-state="by hand"') == 1
        assert "Nothing files a number for this one. You will be asked on the date." in page
        # The test controls carry the stored predicate, the metric by its key and in words.
        assert '<option value="revenue_growth" selected>Revenue growth</option>' in broke
        assert 'value="8"' in broke
        # §10.2: the rule, and the count the panel keeps.
        assert 'data-field="testable">3 of 4<' in page
        assert 'id="the-rule"' in page

    async def test_a_broken_premise_offers_three_answers(
        self, api: Any, scene: dict[str, Any]
    ) -> None:
        editor = await _editor_scene(scene)

        page = (await api.get(editor["location"])).text

        assert 'id="broken"' in page
        assert "One premise broke" in page
        jid = editor["broke"].judgement_id
        assert f'href="#statement-{jid}"' in page
        assert f'action="/theses/{editor["thesis"].id}/premises/{jid}/withdraw"' in page
        assert f'action="/theses/{editor["thesis"].id}/premises/{jid}/keep"' in page
        assert "Whichever you choose, the old wording is kept." in page

    async def test_saving_the_revision_revises_only_what_changed(
        self, api: Any, scene: dict[str, Any]
    ) -> None:
        editor = await _editor_scene(scene)
        page = (await api.get(editor["location"])).text
        broke = editor["broke"].judgement_id

        saved = await api.post(
            f"{editor['location']}/revise",
            data={
                "csrf_token": _csrf(page),
                f"statement-{broke}": "The pipeline replaces most of the lost revenue.",
                f"metric-{broke}": "revenue_growth",
                f"comparator-{broke}": "at_least",
                f"threshold-{broke}": "4",
                f"unit-{broke}": "percent",
                "reason": "Two launches slipped a year.",
            },
        )

        assert saved.status_code == 303, saved.text
        async with scene["factory"]() as session:
            thesis = await thesis_service.thesis_of(
                session, editor["thesis"].id, user_id=scene["user"].id
            )
            assert thesis is not None
            by_statement = {premise.statement: premise for premise in thesis.premises}
            assert len(thesis.premises) == 5, "one new row; nothing edited in place"
            old = by_statement["The pipeline replaces the lost revenue."]
            new = by_statement["The pipeline replaces most of the lost revenue."]
            assert old.judgement.withdrawn_reason == "Two launches slipped a year."
            assert new.judgement.supersedes_id == old.judgement_id
            assert new.threshold == Decimal(4)
            assert not by_statement["Gross margin holds."].judgement.is_withdrawn
            # The reading about the old wording is closed, as a withdrawal.
            finding = await session.get(Finding, editor["finding"].id)
            assert finding is not None
            await session.refresh(finding, attribute_names=["resolutions"])
            assert [row.action for row in finding.resolutions] == [FindingAction.WITHDRAWN]

        after = (await api.get(editor["location"])).text
        assert "One premise broke" not in after
        history = _row_of(after, new.judgement_id)
        assert "How this premise has changed" in history
        assert "you revised it, because Two launches slipped a year., to:" in history
        # The revised premise sits where the old wording did, first.
        assert after.index(f'id="premise-{new.judgement_id}"') < after.index(
            f'id="premise-{editor["holds"].judgement_id}"'
        )
        assert 'data-field="revisions">1<' in after

    async def test_a_revision_without_a_reason_is_refused_and_keeps_what_was_typed(
        self, api: Any, scene: dict[str, Any]
    ) -> None:
        editor = await _editor_scene(scene)
        page = (await api.get(editor["location"])).text
        holds = editor["holds"].judgement_id

        refused = await api.post(
            f"{editor['location']}/revise",
            data={
                "csrf_token": _csrf(page),
                f"statement-{holds}": "Gross margin holds above 75%.",
                "reason": "",
            },
        )

        assert refused.status_code == 422
        assert 'id="revision-problem"' in refused.text
        assert "needs a reason" in refused.text
        assert "Gross margin holds above 75%." in refused.text, "what was typed survives"
        async with scene["factory"]() as session:
            count = await session.scalar(select(func.count()).select_from(Premise))
            assert count == 4

    async def test_nothing_changed_saves_nothing(self, api: Any, scene: dict[str, Any]) -> None:
        editor = await _editor_scene(scene)
        page = (await api.get(editor["location"])).text
        holds = editor["holds"].judgement_id

        response = await api.post(
            f"{editor['location']}/revise",
            data={
                "csrf_token": _csrf(page),
                # The stored metric by its key, the threshold with its trailing zeros: the
                # same premise, so not a revision.
                f"statement-{holds}": "Gross margin holds.",
                f"metric-{holds}": "gross_margin",
                f"threshold-{holds}": "78.0",
                f"unit-{holds}": "percent",
            },
        )

        assert response.status_code == 303
        assert response.headers["location"].endswith("?unchanged=1")
        after = await api.get(response.headers["location"])
        assert "Nothing had changed, so nothing was saved." in after.text

    async def test_keeping_a_broken_premise_closes_its_reading_as_seen(
        self, api: Any, scene: dict[str, Any]
    ) -> None:
        editor = await _editor_scene(scene)
        page = (await api.get(editor["location"])).text
        jid = editor["broke"].judgement_id

        kept = await api.post(
            f"{editor['location']}/premises/{jid}/keep",
            data={"csrf_token": _csrf(page), "reason": "One soft year; launches are late."},
        )

        assert kept.status_code == 303, kept.text
        async with scene["factory"]() as session:
            finding = await session.get(Finding, editor["finding"].id)
            assert finding is not None
            await session.refresh(finding, attribute_names=["resolutions"])
            assert [row.action for row in finding.resolutions] == [FindingAction.DISMISSED]
            judgement = await session.get(Judgement, jid)
            assert judgement is not None
            assert not judgement.is_withdrawn, "kept means the premise stands"
        after = (await api.get(editor["location"])).text
        assert "One premise broke" not in after
        broke = _row_of(after, jid)
        assert 'data-state="broke"' in broke, "the measurement still says what it said"
        assert "One soft year; launches are late." in broke

    async def test_keeping_needs_a_reason(self, api: Any, scene: dict[str, Any]) -> None:
        editor = await _editor_scene(scene)
        page = (await api.get(editor["location"])).text

        refused = await api.post(
            f"{editor['location']}/premises/{editor['broke'].judgement_id}/keep",
            data={"csrf_token": _csrf(page), "reason": " "},
        )

        assert refused.status_code == 422
        assert "needs a reason" in refused.text

    async def test_withdrawing_a_broken_premise_closes_its_reading(
        self, api: Any, scene: dict[str, Any]
    ) -> None:
        editor = await _editor_scene(scene)
        page = (await api.get(editor["location"])).text
        jid = editor["broke"].judgement_id

        withdrawn = await api.post(
            f"{editor['location']}/premises/{jid}/withdraw",
            data={"csrf_token": _csrf(page), "reason": "The cliff is steeper than I thought."},
        )

        assert withdrawn.status_code == 303, withdrawn.text
        async with scene["factory"]() as session:
            finding = await session.get(Finding, editor["finding"].id)
            assert finding is not None
            await session.refresh(finding, attribute_names=["resolutions"])
            assert [row.action for row in finding.resolutions] == [FindingAction.WITHDRAWN]
        after = (await api.get(editor["location"])).text
        assert 'id="given-up"' in after
        assert "The cliff is steeper than I thought." in after

    async def test_a_gate_with_its_pass_on_record_is_decided_with_the_hash_shown(
        self, api: Any, scene: dict[str, Any]
    ) -> None:
        """A contradiction whose pass is on record opens the thesis gate (ADR 0078); keeping
        it from the editor decides that gate, bound to the finding as the page showed it."""
        editor = await _editor_scene(scene, gate=True)
        page = (await api.get(editor["location"])).text
        assert f'name="payload_hash-{editor["finding"].id}"' in page

        kept = await api.post(
            f"{editor['location']}/premises/{editor['broke'].judgement_id}/keep",
            data={
                "csrf_token": _csrf(page),
                "reason": "One soft year.",
                f"payload_hash-{editor['finding'].id}": re.search(
                    rf'name="payload_hash-{editor["finding"].id}" value="([0-9a-f]+)"', page
                ).group(1),  # type: ignore[union-attr]
            },
        )

        assert kept.status_code == 303, kept.text
        async with scene["factory"]() as session:
            approval = await session.scalar(select(Approval))
            assert approval is not None
            assert approval.gate is GateKind.THESIS
            assert approval.decision is Decision.REJECTED
            assert approval.notes == "One soft year."

    async def test_nothing_testable_is_allowed_and_says_what_it_costs(
        self, api: Any, scene: dict[str, Any]
    ) -> None:
        """§10.2: a thesis with no testable premise saves, and warns in terms of the
        consequence rather than scolding."""
        location = await _written(api, scene)
        token = _csrf((await api.get(location)).text)
        await api.post(
            f"{location}/premises",
            data={
                "csrf_token": token,
                "statement": "Management allocates capital well.",
                "basis": "b",
                "defeated_by": "review",
                "review_by": (datetime.now(UTC).date() + timedelta(days=30)).isoformat(),
            },
        )

        page = (await api.get(location)).text

        assert 'id="nothing-testable"' in page
        assert "the monitor has nothing to watch on your behalf" in page
        assert 'data-field="testable">0 of 1<' in page
