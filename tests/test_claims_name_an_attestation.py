"""The seam a report has to come through to say anything about a holding.

`claims` enforced invariant 3 as a check constraint — exactly one of a financial fact and a
calculation — which is the invariant written in SQL and which meant a report could never
make a numeric claim about a position at all. ADR 0073 admitted the fourth record kind;
this is the arm it comes through.

**The widening admits a third kind of figure and not an unevidenced one**, and the tests
below are arranged around that distinction. The constraint still permits exactly one arm.
The grade still travels, and `_figure_view` still says which it was — a surface that showed
an attested figure without saying so would be making the platform's word into evidence.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from aer.core.enums import (
    AttestationKind,
    ClaimKind,
    Grade,
    JobStatus,
    RequestStatus,
    TransactionKind,
    UserRole,
)
from aer.db.models import (
    Attestation,
    Claim,
    Job,
    Portfolio,
    ReportSection,
    SectionDefinition,
    SectionStatus,
    Security,
    Transaction,
    User,
)
from aer.errors import ValidationError
from aer.services import provenance as provenance_service
from aer.services.citations import record_claim
from tests.request_fixtures import research_request

pytestmark = pytest.mark.integration

DEALT_ON = date(2026, 6, 15)


@pytest.fixture
async def scene(db_session: Any) -> dict[str, Any]:
    """A drafted section, a book, and one trade the operator typed from memory."""
    user = User(email="claims@example.invalid", display_name="C", role=UserRole.OWNER)
    security = Security(
        ticker="MSFT", exchange="NASDAQ", provider_symbol="MSFT.US", quote_currency="USD"
    )
    db_session.add_all([user, security])
    await db_session.flush()

    request = research_request(
        user_id=user.id,
        company_name="Microsoft Corporation",
        ticker="MSFT",
        exchange="NASDAQ",
        as_of_date=DEALT_ON,
        base_currency="USD",
        investment_horizon_months=12,
        max_cost_gbp="2.50",
        portfolio_context={},
        point_in_time=True,
        status=RequestStatus.DRAFT,
    )
    portfolio = Portfolio(user_id=user.id, name="ISA", base_currency="GBP")
    db_session.add_all([request, portfolio])
    await db_session.flush()

    job = Job(
        work_order_id=request.id,
        workflow_version="test",
        code_version="test",
        status=JobStatus.RUNNING,
        started_at=datetime.now(UTC),
    )
    db_session.add(job)
    await db_session.flush()

    definition = await db_session.scalar(
        select(SectionDefinition).order_by(SectionDefinition.position).limit(1)
    )
    section = ReportSection(
        job_id=job.id,
        section_definition_id=definition.id,
        section_key=definition.key,
        position=definition.position,
        status=SectionStatus.GENERATED,
        content={"body": "The account holds Microsoft."},
    )
    attestation = Attestation(
        kind=AttestationKind.TRANSACTION,
        grade=Grade.ATTESTED,
        effective_at=datetime(2026, 6, 15, 16, 30, tzinfo=UTC),
        recorded_by="claims@example.invalid",
    )
    db_session.add_all([section, attestation])
    await db_session.flush()

    trade = Transaction(
        attestation_id=attestation.id,
        portfolio_id=portfolio.id,
        kind=TransactionKind.BUY,
        security_id=security.id,
        trade_date=DEALT_ON,
        quantity=Decimal("100"),
        price=Decimal("410.25"),
        fees=Decimal("9.95"),
        currency="USD",
    )
    db_session.add(trade)
    await db_session.flush()

    return {
        "section": section,
        "attestation": attestation,
        "trade": trade,
        "portfolio": portfolio,
        "security": security,
    }


class TestAReportCanNowAssertSomethingAboutAHolding:
    async def test_a_numeric_claim_may_name_an_attestation(self, db_session, scene) -> None:
        claim = await record_claim(
            db_session,
            section=scene["section"],
            kind=ClaimKind.NUMERIC,
            text="The account bought 100 Microsoft shares on 15 June 2026.",
            attestation_id=scene["attestation"].id,
        )

        assert claim.attestation_id == scene["attestation"].id
        assert claim.financial_fact_id is None
        assert claim.calculation_id is None

    async def test_it_is_still_exactly_one_arm(self, db_session, scene) -> None:
        """A sentence asserting two numbers, refused before it exists.

        With both columns set, which figure a reader was shown would depend on which one
        the renderer happened to check first.
        """
        with pytest.raises(ValidationError, match="exactly one figure"):
            await record_claim(
                db_session,
                section=scene["section"],
                kind=ClaimKind.NUMERIC,
                text="Two figures, one sentence.",
                attestation_id=scene["attestation"].id,
                calculation_id=uuid.uuid4(),
            )

    async def test_the_database_refuses_it_too(self, db_session, scene) -> None:
        # The service check exists to give a better message, not to be the control. A
        # writer that went round it meets the same rule.
        db_session.add(
            Claim(
                report_section_id=scene["section"].id,
                kind=ClaimKind.NUMERIC,
                text="A number nobody named.",
            )
        )

        with pytest.raises(IntegrityError, match="ck_claims_numeric_claims_name_one_figure"):
            await db_session.flush()

    async def test_an_opinion_still_may_not_carry_one(self, db_session, scene) -> None:
        # The half of the constraint that is about the other direction: a figure attached
        # to a statement nothing checks would look verified to every reader downstream.
        with pytest.raises(ValidationError, match="must not name a figure"):
            await record_claim(
                db_session,
                section=scene["section"],
                kind=ClaimKind.OPINION,
                text="The position looks well sized.",
                attestation_id=scene["attestation"].id,
            )


class TestTheFigureAClaimShows:
    async def test_it_is_what_moved_rather_than_what_it_cost(self, db_session, scene) -> None:
        """A trade carries three numbers and a claim names a row, so something must choose.

        The signed quantity is the only one true of every kind — a dividend has no price
        and a deposit has no security — and the rest travel in the detail where a reader
        can see them.
        """
        claim = await record_claim(
            db_session,
            section=scene["section"],
            kind=ClaimKind.NUMERIC,
            text="The account bought 100 Microsoft shares.",
            attestation_id=scene["attestation"].id,
        )

        figure = await provenance_service._figure_view(db_session, claim)

        assert figure is not None
        assert figure.kind == "attestation"
        # Compared as decimals rather than as text: the scale a Numeric round-trips at is
        # the database's business, and an assertion on the trailing zeros would fail the
        # day the column width changed for reasons nothing to do with this.
        assert Decimal(figure.value) == Decimal("100")
        assert figure.unit == "shares"
        assert Decimal(figure.detail["price"]) == Decimal("410.25")
        assert Decimal(figure.detail["fees"]) == Decimal("9.95")

    async def test_a_cash_figure_is_measured_in_its_currency(self, db_session, scene) -> None:
        # Fifty pounds is fifty pounds and not fifty of anything else. A single "shares"
        # unit would make a dividend read as a share count.
        cash = Attestation(
            kind=AttestationKind.TRANSACTION,
            grade=Grade.ATTESTED,
            effective_at=datetime(2026, 3, 3, 12, 0, tzinfo=UTC),
            recorded_by="claims@example.invalid",
        )
        db_session.add(cash)
        await db_session.flush()
        db_session.add(
            Transaction(
                attestation_id=cash.id,
                portfolio_id=scene["portfolio"].id,
                kind=TransactionKind.DEPOSIT,
                trade_date=date(2026, 3, 3),
                quantity=Decimal("50"),
                currency="GBP",
            )
        )
        await db_session.flush()

        claim = await record_claim(
            db_session,
            section=scene["section"],
            kind=ClaimKind.NUMERIC,
            text="Fifty pounds was paid in.",
            attestation_id=cash.id,
        )
        figure = await provenance_service._figure_view(db_session, claim)

        assert figure is not None
        assert figure.unit == "GBP"

    async def test_the_grade_is_on_the_figure_a_surface_receives(self, db_session, scene) -> None:
        """The field a renderer has to read, and the reason the arm is safe to have.

        An attested figure is one nobody documented. A surface that showed it beside a
        filing's number with nothing to tell them apart would be making the platform's word
        into evidence.
        """
        claim = await record_claim(
            db_session,
            section=scene["section"],
            kind=ClaimKind.NUMERIC,
            text="The account bought 100 Microsoft shares.",
            attestation_id=scene["attestation"].id,
        )

        figure = await provenance_service._figure_view(db_session, claim)

        assert figure is not None
        assert figure.detail["grade"] == Grade.ATTESTED.value
        assert figure.detail["recorded_by"] == "claims@example.invalid"
        # Empty, because this one is typed. A documented row names its contract note here,
        # and the difference is what the whole grade distinction is for.
        assert figure.detail["source_document_id"] == ""


class TestTheEvidenceCannotBeDeletedFromUnderAPublishedClaim:
    async def test_an_attestation_a_claim_names_cannot_be_removed(self, db_session, scene) -> None:
        """`RESTRICT`, and it bites harder here than on the other two arms.

        An attestation is corrected by a superseding row rather than an update, so the row
        a published claim named stays exactly as it was written — and deleting it would
        leave a published number with nothing behind it.
        """
        await record_claim(
            db_session,
            section=scene["section"],
            kind=ClaimKind.NUMERIC,
            text="The account bought 100 Microsoft shares.",
            attestation_id=scene["attestation"].id,
        )

        removal = Attestation.__table__.delete().where(
            Attestation.__table__.c.id == scene["attestation"].id
        )

        # `RESTRICT` is checked immediately, so the statement itself is what refuses.
        with pytest.raises(IntegrityError):
            await db_session.execute(removal)


class TestMacroIsStillASeamAndThisIsNotIt:
    def test_the_constraint_admits_three_arms_and_not_a_published_statistic(self) -> None:
        """ADR 0073 named this and picked neither answer; nothing here picks one either.

        A gilt yield is neither a financial fact nor an attestation, so it still reaches a
        report only wrapped in a calculation. Asserted against the column list rather than
        left as prose, so a fourth arm arriving quietly would fail here.
        """
        figures = {
            column.name
            for column in Claim.__table__.columns
            if column.name.endswith("_id") and column.name != "report_section_id"
        }

        assert figures == {"financial_fact_id", "calculation_id", "attestation_id"}
