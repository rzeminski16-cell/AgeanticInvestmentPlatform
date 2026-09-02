"""A decision is written before the outcome, and the trade points back at it.

Three layers. The service proves the record: a decision is a judgement with a holder, a
time and a basis, seen from its consequence; a withdrawal leaves the row; a revision is a
new row that supersedes the old; a trade carries a decision out by pointing at it, and a
pairing that cannot be what it claims is refused. The structural tests prove ADR 0074 still
holds with a second subtype and a column on the trade — the judgement enters no lineage,
and `aer.calc` has no word for a decision. And the pages prove a person can do all of it,
including recording the trade against the decision from the portfolio form.
"""

from __future__ import annotations

import ast
import re
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import aer.calc
from aer.core.enums import (
    AttestationKind,
    DecisionAction,
    Grade,
    JudgementKind,
    TransactionKind,
    UserRole,
)
from aer.db.base import Base
from aer.db.models import (
    Attestation,
    AuditEvent,
    Company,
    Portfolio,
    Security,
    Thesis,
    Transaction,
    User,
)
from aer.errors import ConflictError, ValidationError
from aer.services import decisions as decision_service
from aer.services import theses as thesis_service
from aer.web.overview import decisions as decision_feed
from aer.web.overview.attention import Severity
from tests.api_fixtures import build_app, client_for

pytestmark = pytest.mark.integration


async def _user(session: AsyncSession, email: str = "decider@example.invalid") -> User:
    user = User(email=email, display_name="Decider", role=UserRole.OWNER)
    session.add(user)
    await session.flush()
    return user


async def _company(session: AsyncSession) -> Company:
    company = Company(name="Contoso plc", ticker="CTSO", exchange="LSE", company_number="01234567")
    session.add(company)
    await session.flush()
    return company


async def _security(session: AsyncSession, company: Company) -> Security:
    security = Security(
        company_id=company.id,
        ticker="CTSO",
        exchange="LSE",
        provider_symbol="CTSO.LSE",
        name="Contoso plc",
        quote_currency="GBX",
    )
    session.add(security)
    await session.flush()
    return security


async def _thesis(session: AsyncSession, user: User, company: Company) -> Thesis:
    thesis = await thesis_service.write_thesis(
        session, user=user, company=company, title="Contoso keeps compounding"
    )
    await thesis_service.add_premise(
        session,
        thesis=thesis,
        actor=user,
        statement="Management allocates capital well.",
        basis="Ten years of buybacks below intrinsic value.",
        predicate=None,
        review_by=date(2027, 3, 31),
    )
    loaded = await thesis_service.thesis_of(session, thesis.id, user_id=user.id)
    assert loaded is not None
    return loaded


async def _decision(session: AsyncSession, user: User, thesis: Thesis, **overrides: Any) -> Any:
    fields: dict[str, Any] = {
        "action": DecisionAction.BUY,
        "statement": "Open an initial position, about two per cent of the book.",
        "basis": "The FY25 report confirmed the margin structure.",
        "size_statement": "about 2% of the book",
        "horizon_months": 24,
        "exit_plan": "Sell if operating margin falls below 20% for two years running.",
    }
    fields.update(overrides)
    return await decision_service.record_decision(session, actor=user, thesis=thesis, **fields)


async def _trade(
    session: AsyncSession,
    *,
    book: Portfolio,
    security: Security | None,
    kind: TransactionKind = TransactionKind.BUY,
    quantity: Decimal = Decimal(100),
) -> Transaction:
    attestation = Attestation(
        kind=AttestationKind.TRANSACTION,
        grade=Grade.ATTESTED,
        effective_at=datetime(2026, 6, 15, tzinfo=UTC),
        recorded_by="decider@example.invalid",
    )
    session.add(attestation)
    await session.flush()
    trade = Transaction(
        attestation_id=attestation.id,
        portfolio_id=book.id,
        kind=kind,
        security_id=security.id if security is not None else None,
        trade_date=date(2026, 6, 15),
        quantity=quantity if kind in (TransactionKind.BUY, TransactionKind.DEPOSIT) else -quantity,
        price=Decimal("1250") if kind in (TransactionKind.BUY, TransactionKind.SELL) else None,
        fees=Decimal(0),
        currency="GBX" if security is not None else "GBP",
    )
    session.add(trade)
    await session.flush()
    return trade


@pytest.fixture
async def scene(db_session: AsyncSession) -> dict[str, Any]:
    user = await _user(db_session)
    company = await _company(db_session)
    security = await _security(db_session, company)
    book = Portfolio(user_id=user.id, name="My book", base_currency="GBP")
    db_session.add(book)
    await db_session.flush()
    thesis = await _thesis(db_session, user, company)
    return {
        "session": db_session,
        "user": user,
        "company": company,
        "security": security,
        "book": book,
        "thesis": thesis,
    }


# -- The record --------------------------------------------------------------------------------


class TestADecisionIsAJudgementSeenFromItsConsequence:
    async def test_it_is_written_with_a_holder_a_time_and_a_basis(
        self, scene: dict[str, Any]
    ) -> None:
        decided = datetime(2026, 8, 1, tzinfo=UTC)

        decision = await _decision(
            scene["session"],
            scene["user"],
            scene["thesis"],
            security=scene["security"],
            portfolio=scene["book"],
            decided_at=decided,
            review_by=date(2026, 12, 1),
        )

        assert decision.judgement.kind is JudgementKind.DECISION
        assert decision.judgement.held_by == scene["user"].email
        assert decision.judgement.held_at == decided
        assert decision.judgement.recorded_at >= decided
        assert decision.judgement.basis == "The FY25 report confirmed the margin structure."
        assert decision.action is DecisionAction.BUY
        assert decision.size_statement == "about 2% of the book"
        assert decision.horizon_months == 24
        assert decision.security_id == scene["security"].id
        assert decision.portfolio_id == scene["book"].id
        assert decision.transactions == []

    async def test_the_size_is_a_sentence_and_the_schema_has_no_number_for_it(self) -> None:
        """ADR 0104 §1: an intended weight stored as a number would be a judgement wearing a
        Quantity's clothes. The column is text; there is no numeric size column at all."""
        columns = Base.metadata.tables["decisions"].columns
        assert columns["size_statement"].type.python_type is str
        suspicious = re.compile(r"weight|size_|quantity|amount|price|convict|score", re.IGNORECASE)
        numeric = [
            column.name
            for column in columns
            if suspicious.search(column.name) and column.type.python_type is not str
        ]
        assert not numeric, numeric

    async def test_a_blank_statement_or_basis_is_refused(self, scene: dict[str, Any]) -> None:
        with pytest.raises(ValidationError, match="states nothing"):
            await _decision(scene["session"], scene["user"], scene["thesis"], statement=" ")
        with pytest.raises(ValidationError, match="stated basis"):
            await _decision(scene["session"], scene["user"], scene["thesis"], basis="")

    async def test_a_horizon_is_a_positive_number_of_months(self, scene: dict[str, Any]) -> None:
        with pytest.raises(ValidationError, match="positive number of months"):
            await _decision(scene["session"], scene["user"], scene["thesis"], horizon_months=0)

    async def test_a_retired_thesis_takes_no_decision(self, scene: dict[str, Any]) -> None:
        await thesis_service.retire_thesis(
            scene["session"], thesis=scene["thesis"], actor=scene["user"], reason="Replaced."
        )

        with pytest.raises(ConflictError, match="retired"):
            await _decision(scene["session"], scene["user"], scene["thesis"])

    async def test_somebody_elses_thesis_takes_no_decision_of_yours(
        self, scene: dict[str, Any]
    ) -> None:
        other = await _user(scene["session"], email="other@example.invalid")

        with pytest.raises(ConflictError, match="person whose thesis"):
            await _decision(scene["session"], other, scene["thesis"])

    async def test_every_write_is_on_the_chain_with_the_decision_as_its_subject(
        self, scene: dict[str, Any]
    ) -> None:
        decision = await _decision(scene["session"], scene["user"], scene["thesis"])
        await decision_service.withdraw_decision(
            scene["session"], decision=decision, actor=scene["user"], reason="Changed my mind."
        )

        events = list(
            await scene["session"].scalars(
                select(AuditEvent)
                .where(AuditEvent.subject_id == decision.judgement_id)
                .order_by(AuditEvent.id)
            )
        )
        assert [event.event_type for event in events] == [
            "decision.recorded",
            "decision.withdrawn",
        ]
        assert all(event.subject_kind == "decision" for event in events)
        assert events[1].payload["reason"] == "Changed my mind."


class TestNothingIsEditedAndNothingIsDeleted:
    async def test_withdrawing_needs_a_reason_and_happens_once(self, scene: dict[str, Any]) -> None:
        decision = await _decision(scene["session"], scene["user"], scene["thesis"])

        with pytest.raises(ValidationError, match="needs a reason"):
            await decision_service.withdraw_decision(
                scene["session"], decision=decision, actor=scene["user"], reason="  "
            )
        await decision_service.withdraw_decision(
            scene["session"], decision=decision, actor=scene["user"], reason="No longer."
        )
        with pytest.raises(ConflictError, match="already withdrawn"):
            await decision_service.withdraw_decision(
                scene["session"], decision=decision, actor=scene["user"], reason="Again."
            )

        assert decision.judgement.withdrawn_reason == "No longer."
        held = await decision_service.decisions_for(scene["session"], user_id=scene["user"].id)
        gone = await decision_service.decisions_for(
            scene["session"], user_id=scene["user"].id, withdrawn=True
        )
        assert held == []
        assert [row.judgement_id for row in gone] == [decision.judgement_id]

    async def test_a_revision_is_a_new_row_that_supersedes_the_old(
        self, scene: dict[str, Any]
    ) -> None:
        """Never an edit: the earlier row keeps what was decided then, withdrawn as
        superseded, and the new row points at it once through the judgement's own link."""
        first = await _decision(scene["session"], scene["user"], scene["thesis"])

        revised = await decision_service.revise_decision(
            scene["session"],
            decision=first,
            actor=scene["user"],
            thesis=scene["thesis"],
            action=DecisionAction.ADD,
            statement="Add to the position after the results.",
            basis="The results confirmed the second premise.",
            size_statement="another 1%",
            horizon_months=18,
        )

        assert revised.judgement_id != first.judgement_id
        assert revised.judgement.supersedes_id == first.judgement_id
        assert revised.action is DecisionAction.ADD
        assert first.statement == "Open an initial position, about two per cent of the book."
        assert first.judgement.is_withdrawn
        assert first.judgement.withdrawn_reason is not None
        assert first.judgement.withdrawn_reason.startswith("Superseded by a later decision")

        with pytest.raises(ConflictError, match="already superseded"):
            await decision_service.revise_decision(
                scene["session"],
                decision=first,
                actor=scene["user"],
                thesis=scene["thesis"],
                action=DecisionAction.SELL,
                statement="s",
                basis="b",
            )


class TestTheTradePointsAtTheDecision:
    async def test_a_buy_carries_out_a_decision_to_buy(self, scene: dict[str, Any]) -> None:
        decision = await _decision(
            scene["session"], scene["user"], scene["thesis"], security=scene["security"]
        )
        trade = await _trade(scene["session"], book=scene["book"], security=scene["security"])

        await decision_service.carry_out(
            scene["session"], transaction=trade, decision=decision, actor=scene["user"]
        )

        assert trade.decision_id == decision.judgement_id
        reloaded = await decision_service.decision_of(
            scene["session"], decision.judgement_id, user_id=scene["user"].id
        )
        assert reloaded is not None
        assert [row.attestation_id for row in reloaded.transactions] == [trade.attestation_id]
        witnessed = await scene["session"].scalar(
            select(AuditEvent).where(
                AuditEvent.event_type == "decision.carried_out",
                AuditEvent.subject_id == decision.judgement_id,
            )
        )
        assert witnessed is not None

    async def test_a_sale_cannot_carry_out_a_decision_to_buy(self, scene: dict[str, Any]) -> None:
        decision = await _decision(scene["session"], scene["user"], scene["thesis"])
        trade = await _trade(
            scene["session"],
            book=scene["book"],
            security=scene["security"],
            kind=TransactionKind.SELL,
        )

        with pytest.raises(ValidationError, match="cannot carry out"):
            await decision_service.carry_out(
                scene["session"], transaction=trade, decision=decision, actor=scene["user"]
            )
        assert trade.decision_id is None

    async def test_a_trade_in_another_security_is_refused(self, scene: dict[str, Any]) -> None:
        decision = await _decision(
            scene["session"], scene["user"], scene["thesis"], security=scene["security"]
        )
        other = Security(
            ticker="FABR", exchange="LSE", provider_symbol="FABR.LSE", quote_currency="GBX"
        )
        scene["session"].add(other)
        await scene["session"].flush()
        trade = await _trade(scene["session"], book=scene["book"], security=other)

        with pytest.raises(ValidationError, match="different security"):
            await decision_service.carry_out(
                scene["session"], transaction=trade, decision=decision, actor=scene["user"]
            )

    async def test_a_decision_to_hold_is_carried_out_by_nothing(
        self, scene: dict[str, Any]
    ) -> None:
        decision = await _decision(
            scene["session"], scene["user"], scene["thesis"], action=DecisionAction.HOLD
        )
        trade = await _trade(scene["session"], book=scene["book"], security=scene["security"])

        with pytest.raises(ValidationError, match="cannot carry out"):
            await decision_service.carry_out(
                scene["session"], transaction=trade, decision=decision, actor=scene["user"]
            )
        offered = await decision_service.open_for_the_book(
            scene["session"], user_id=scene["user"].id
        )
        assert offered == []

    async def test_a_withdrawn_decision_is_not_carried_out(self, scene: dict[str, Any]) -> None:
        decision = await _decision(scene["session"], scene["user"], scene["thesis"])
        await decision_service.withdraw_decision(
            scene["session"], decision=decision, actor=scene["user"], reason="No."
        )
        trade = await _trade(scene["session"], book=scene["book"], security=scene["security"])

        with pytest.raises(ValidationError, match="withdrawn decision"):
            await decision_service.carry_out(
                scene["session"], transaction=trade, decision=decision, actor=scene["user"]
            )

    async def test_the_link_survives_the_decision_being_withdrawn_and_the_trade_stays_a_trade(
        self, scene: dict[str, Any]
    ) -> None:
        decision = await _decision(scene["session"], scene["user"], scene["thesis"])
        trade = await _trade(scene["session"], book=scene["book"], security=scene["security"])
        await decision_service.carry_out(
            scene["session"], transaction=trade, decision=decision, actor=scene["user"]
        )
        await decision_service.withdraw_decision(
            scene["session"], decision=decision, actor=scene["user"], reason="Regretted."
        )

        assert trade.decision_id == decision.judgement_id
        assert trade.quantity == Decimal(100)


# -- ADR 0074 still holds -------------------------------------------------------------------


class TestAJudgementStillEntersNoLineage:
    def test_only_the_two_subtypes_reference_judgements(self) -> None:
        referrers = sorted(
            table.name
            for table in Base.metadata.sorted_tables
            for key in table.foreign_keys
            if key.column.table.name == "judgements" and table.name != "judgements"
        )
        assert referrers == ["decisions", "premises"]

    def test_the_trade_points_at_the_decision_and_not_the_other_way_round(self) -> None:
        """ADR 0104 §2: the column is on the attestation. `decisions` references a thesis,
        a book and a security — never a transaction, a fact or a calculation."""
        decision_targets = {
            key.column.table.name for key in Base.metadata.tables["decisions"].foreign_keys
        }
        assert decision_targets == {"judgements", "theses", "portfolios", "securities"}
        trade_targets = {
            key.column.table.name for key in Base.metadata.tables["transactions"].foreign_keys
        }
        assert "decisions" in trade_targets

    def test_the_calculation_kernel_has_no_name_for_a_decision(self) -> None:
        """What a position's arithmetic reads off a trade is its quantity, price and fees.
        A calculation that reached for the link would be a red build here, not a laundered
        figure somewhere a reviewer would never look.

        Names, not words: the kernel's prose says "decision" where it means one, and a
        docstring is not a lineage. What must not exist is an identifier — a variable, an
        attribute, a parameter — through which a decision row could reach arithmetic.
        """
        root = Path(aer.calc.__file__).parent
        offenders: list[str] = []
        for path in root.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                name = getattr(node, "id", None) or getattr(node, "attr", None)
                if name is None and isinstance(node, ast.arg):
                    name = node.arg
                if isinstance(name, str) and "decision" in name.lower():
                    offenders.append(f"{path.name}:{name}")
        assert not offenders, offenders


# -- The work list ---------------------------------------------------------------------------


class TestWhatTheJournalPutsInFrontOfTheOperator:
    async def test_a_decision_not_carried_out_and_a_review_due_are_not_started(
        self, scene: dict[str, Any]
    ) -> None:
        undone = await _decision(scene["session"], scene["user"], scene["thesis"])
        due = await _decision(
            scene["session"],
            scene["user"],
            scene["thesis"],
            action=DecisionAction.HOLD,
            statement="Keep holding through the results.",
            review_by=date(2020, 1, 1),
        )
        await _decision(
            scene["session"],
            scene["user"],
            scene["thesis"],
            action=DecisionAction.PASS,
            statement="Not at this price.",
            review_by=datetime.now(UTC).date() + timedelta(days=30),
        )

        items = await decision_feed.items(scene["session"], user_id=scene["user"].id)

        keys = {item.key for item in items}
        assert f"decisions.undone.{undone.judgement_id}" in keys
        assert f"decisions.review.{due.judgement_id}" in keys
        assert len(items) == 2
        assert all(item.severity is Severity.IDLE for item in items)
        assert all(item.href.startswith("/decisions/") for item in items)


# -- The pages -------------------------------------------------------------------------------


_TABLES = "audit_events, users, companies, securities, portfolios, theses, judgements"


@pytest.fixture
async def committed(db_engine: Any) -> Any:
    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    async with factory() as session:
        user = await _user(session, email="owner@example.invalid")
        company = await _company(session)
        security = await _security(session, company)
        book = Portfolio(user_id=user.id, name="My book", base_currency="GBP")
        session.add(book)
        await session.flush()
        thesis = await _thesis(session, user, company)
        await session.commit()
        yield {
            "session": session,
            "user": user,
            "company": company,
            "security": security,
            "book": book,
            "thesis": thesis,
        }
    async with db_engine.begin() as connection:
        await connection.execute(text(f"TRUNCATE {_TABLES} RESTART IDENTITY CASCADE"))


@pytest.fixture
async def api(api_settings: Any, db_engine: Any, fake_redis: Any, committed: Any) -> Any:
    async for client in client_for(build_app(api_settings, engine=db_engine, redis=fake_redis)):
        yield client


def _csrf(html: str) -> str:
    found = re.search(r'name="csrf_token"\s+value="([^"]+)"', html)
    assert found is not None, "the page rendered no CSRF token"
    return found.group(1)


async def _recorded(api: Any, committed: dict[str, Any], **overrides: str) -> str:
    page = await api.get("/decisions")
    data = {
        "csrf_token": _csrf(page.text),
        "thesis_id": str(committed["thesis"].id),
        "action": "buy",
        "statement": "Open an initial position.",
        "basis": "The FY25 report.",
        "security": "CTSO.LSE",
        "size_statement": "about 2% of the book",
        "horizon_months": "24",
        "exit_plan": "Sell below a 20% margin.",
        "decided_on": "2026-08-01",
    }
    data.update(overrides)
    response = await api.post("/decisions", data=data)
    assert response.status_code == 303, response.text
    return str(response.headers["location"])


class TestThePages:
    async def test_a_decision_is_recorded_and_opened(self, api: Any, committed: Any) -> None:
        location = await _recorded(api, committed)

        opened = await api.get(location)

        assert opened.status_code == 200
        assert "Open a position: Open an initial position." in opened.text
        assert 'data-field="size">about 2% of the book' in opened.text
        assert "24 months" in opened.text
        assert "Contoso plc (CTSO)" in opened.text
        assert "Management allocates capital well." in opened.text
        assert "Not yet carried out" in opened.text
        listing = await api.get("/decisions")
        assert "one decision is held" in listing.text.lower()
        assert 'data-carried-out="no"' in listing.text

    async def test_a_listing_the_platform_does_not_hold_is_refused_on_the_page(
        self, api: Any, committed: Any
    ) -> None:
        page = await api.get("/decisions")
        refused = await api.post(
            "/decisions",
            data={
                "csrf_token": _csrf(page.text),
                "thesis_id": str(committed["thesis"].id),
                "action": "buy",
                "statement": "s",
                "basis": "b",
                "security": "NOPE.LSE",
            },
        )
        assert refused.status_code == 400
        assert "no listing" in refused.text

    async def test_the_trade_form_carries_the_decision_out(self, api: Any, committed: Any) -> None:
        """The journal's other half: the trade recorded on the portfolio form names the
        decision, and the decision page then lists the trade."""
        location = await _recorded(api, committed)
        decision_id = location.rsplit("/", 1)[1]
        portfolio = await api.get("/portfolio")
        assert f'<option value="{decision_id}">' in portfolio.text

        recorded = await api.post(
            "/portfolio/transactions",
            data={
                "csrf_token": _csrf(portfolio.text),
                "kind": "buy",
                "security": "CTSO.LSE",
                "trade_date": "2026-08-02",
                "quantity": "100",
                "price": "1250",
                "currency": "GBX",
                "fees": "0",
                "decision": decision_id,
            },
        )
        assert recorded.status_code == 303, recorded.text

        opened = await api.get(location)
        assert 'data-trade="buy"' in opened.text
        assert "Not yet carried out" not in opened.text
        listing = await api.get("/decisions")
        assert 'data-carried-out="yes"' in listing.text
        # And no longer offered on the trade form as something to carry out? Still is: a
        # decision to buy may be carried out in more than one fill. What it is not is undone.
        assert "not yet carried out" not in listing.text

    async def test_a_sale_cannot_be_recorded_against_a_decision_to_buy(
        self, api: Any, committed: Any
    ) -> None:
        location = await _recorded(api, committed)
        decision_id = location.rsplit("/", 1)[1]
        portfolio = await api.get("/portfolio")

        refused = await api.post(
            "/portfolio/transactions",
            data={
                "csrf_token": _csrf(portfolio.text),
                "kind": "sell",
                "security": "CTSO.LSE",
                "trade_date": "2026-08-02",
                "quantity": "100",
                "price": "1250",
                "currency": "GBX",
                "fees": "0",
                "decision": decision_id,
            },
        )

        assert refused.status_code == 422
        assert "cannot carry out" in refused.text
        assert (await api.get(location)).text.count("data-trade=") == 0

    async def test_revising_supersedes_and_withdrawing_keeps_the_row(
        self, api: Any, committed: Any
    ) -> None:
        location = await _recorded(api, committed)
        page = await api.get(location)

        revised = await api.post(
            f"{location}/revise",
            data={
                "csrf_token": _csrf(page.text),
                "action": "add",
                "statement": "Add after the results.",
                "basis": "The results confirmed it.",
                "security": "CTSO.LSE",
                "size_statement": "another 1%",
                "horizon_months": "18",
            },
        )
        assert revised.status_code == 303
        new_location = str(revised.headers["location"])
        assert new_location != location
        assert "Add to the position: Add after the results." in (await api.get(new_location)).text
        old = await api.get(location)
        assert 'id="withdrawn-notice"' in old.text
        assert "Superseded by a later decision" in old.text
        assert 'id="revise-decision"' not in old.text

        withdrawn = await api.post(
            f"{new_location}/withdraw",
            data={"csrf_token": _csrf((await api.get(new_location)).text), "reason": "Cold feet."},
        )
        assert withdrawn.status_code == 303
        assert "Cold feet." in (await api.get(new_location)).text
        assert "Add after the results." in (await api.get("/decisions?withdrawn=1")).text

    async def test_the_thesis_page_lists_its_decisions(self, api: Any, committed: Any) -> None:
        location = await _recorded(api, committed)

        thesis_page = await api.get(f"/theses/{committed['thesis'].id}")

        assert f'data-decision="{location.rsplit("/", 1)[1]}"' in thesis_page.text
        assert "Open a position: Open an initial position." in thesis_page.text

    async def test_a_decision_that_is_not_yours_answers_as_missing(self, api: Any) -> None:
        assert (await api.get(f"/decisions/{uuid.uuid4()}")).status_code == 404
