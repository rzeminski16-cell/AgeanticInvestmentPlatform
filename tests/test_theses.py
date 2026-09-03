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
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aer.calc.units import SourceKind, SourceRef, SourceTable
from aer.core.enums import JudgementKind, PremiseComparator, UserRole
from aer.db.base import Base
from aer.db.models import AuditEvent, Company, Judgement, Premise, Thesis, User
from aer.errors import ConflictError, ValidationError
from aer.services import theses as thesis_service
from aer.services.theses import Predicate
from tests.api_fixtures import build_app, client_for

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


_TABLES = "audit_events, users, companies, theses, judgements"


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
        assert "Nothing is asserted yet" in opened.text

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
        assert "2 premises held" in opened.text
        assert "1 tested by a threshold, 1 reviewed by a person" in opened.text
        assert "revenue growth at least 25 percent" in opened.text
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
        assert "1 withdrawn, with the reason kept" in after.text

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
