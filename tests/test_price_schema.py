"""The price schema's refusals, exercised against the database rather than the model.

Alembic's autogenerate does not compare CHECK constraints at all, so a model whose checks
disagreed with the migration's would produce no drift and no failure. These tests therefore
insert rows that ought to be rejected and assert that Postgres rejects them — which is the
only way the constraints in `migrations/versions/0018` are known to exist.

The pair of partial unique indexes on `corporate_actions` gets the most attention, because
the obvious single constraint is wrong in both directions: one over `(security, kind,
ex_date)` rejects a real ordinary-plus-special dividend, and one over the amount alone lets a
duplicated split through.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError

from aer.core.enums import Provider, SourceTier, UserRole
from aer.db.models import (
    Artefact,
    Company,
    CorporateAction,
    CorporateActionKind,
    PriceBar,
    ResearchRequest,
    Security,
    SourceDocument,
    User,
)
from tests.workflow_fixtures import AS_OF_DATE

pytestmark = pytest.mark.integration

_TABLES = "companies, securities, research_requests, users, artefacts"


@pytest.fixture
async def security(db_session: Any) -> Security:
    await db_session.execute(text(f"TRUNCATE {_TABLES} RESTART IDENTITY CASCADE"))
    row = Security(
        ticker="MSFT",
        exchange="NASDAQ",
        provider_symbol="MSFT.US",
        name="Microsoft Corporation",
        quote_currency="USD",
    )
    db_session.add(row)
    await db_session.flush()
    return row


@pytest.fixture
async def scene(db_session: Any, security: Security) -> ResearchRequest:
    """The minimum a `source_documents` row needs upstream of it."""
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
    return request


async def provenance(session: Any, request: ResearchRequest) -> SourceDocument:
    """An archived price response, the way an acquisition would leave it."""
    artefact = Artefact(
        sha256="a" * 64,
        media_type="application/json",
        size_bytes=64,
        storage_key="sha256/aa/aa/" + "a" * 64,
    )
    session.add(artefact)
    await session.flush()

    document = SourceDocument(
        artefact_id=artefact.id,
        work_order_id=request.id,
        request_id=request.id,
        provider=Provider.EODHD,
        source_tier=SourceTier.T4_LICENSED_MARKET,
        url="https://eodhd.invalid/api/eod/MSFT.US",
        retrieved_at=datetime.now(UTC),
    )
    session.add(document)
    await session.flush()
    return document


def bar(security: Security, **overrides: Any) -> PriceBar:
    defaults: dict[str, Any] = {
        "security_id": security.id,
        "bar_date": date(2024, 6, 28),
        "open": Decimal("446.00"),
        "high": Decimal("448.00"),
        "low": Decimal("445.00"),
        "close": Decimal("446.95"),
        "volume": 15_000_000,
    }
    return PriceBar(**(defaults | overrides))


# -- The listing -------------------------------------------------------------------------


class TestASecurityIsOneListing:
    async def test_two_rows_for_one_listing_are_refused(self, db_session, security):
        db_session.add(
            Security(
                ticker="MSFT",
                exchange="NASDAQ",
                provider_symbol="MSFT.US.DUPLICATE",
                quote_currency="USD",
            )
        )
        with pytest.raises(IntegrityError):
            await db_session.flush()

    async def test_two_rows_for_one_vendor_symbol_are_refused(self, db_session, security):
        db_session.add(
            Security(
                ticker="MSFT2",
                exchange="NASDAQ",
                provider_symbol="MSFT.US",
                quote_currency="USD",
            )
        )
        with pytest.raises(IntegrityError):
            await db_session.flush()

    async def test_a_lowercase_quote_currency_is_refused(self, db_session, security):
        """Comparisons against it would silently fail, which is worse than an error here."""
        db_session.add(
            Security(
                ticker="BARC",
                exchange="LSE",
                provider_symbol="BARC.LSE",
                quote_currency="gbx",
            )
        )
        with pytest.raises(IntegrityError) as excinfo:
            await db_session.flush()
        assert "quote_currency_is_upper" in str(excinfo.value)

    async def test_a_blank_ticker_is_refused(self, db_session, security):
        """`String(32)` permits the empty string, so the check is what makes the column mean
        something."""
        db_session.add(
            Security(
                ticker="",
                exchange="LSE",
                provider_symbol="EMPTY.LSE",
                quote_currency="GBX",
            )
        )
        with pytest.raises(IntegrityError) as excinfo:
            await db_session.flush()
        assert "has_a_ticker" in str(excinfo.value)

    async def test_pence_is_recorded_as_its_own_currency(self, db_session, security):
        """A London listing quotes in pence, and 250 means £2.50.

        Stored as `GBX` rather than normalised to `GBP` on the way in, so a bar always means
        what the exchange printed and the conversion is a step somebody can see.
        """
        db_session.add(
            Security(
                ticker="BARC",
                exchange="LSE",
                provider_symbol="BARC.LSE",
                quote_currency="GBX",
            )
        )
        await db_session.flush()

        found = await db_session.scalar(
            select(Security).where(Security.provider_symbol == "BARC.LSE")
        )
        assert found is not None
        assert found.quote_currency == "GBX"

    async def test_a_security_can_exist_without_a_company(self, db_session, security):
        """A peer's price series is worth having before the peer is resolved anywhere."""
        assert security.company_id is None


# -- The bars ----------------------------------------------------------------------------


class TestAPriceBarHasToBeAPriceBar:
    async def test_a_valid_bar_is_accepted(self, db_session, security):
        db_session.add(bar(security))
        await db_session.flush()

        stored = await db_session.scalar(select(PriceBar))
        assert stored is not None
        assert stored.close == Decimal("446.950000")

    async def test_two_bars_for_one_day_collide(self, db_session, security):
        """A vendor correcting history therefore surfaces rather than overwriting.

        The correction routes into the disagreement ladder instead of quietly changing a
        number a report already cited.
        """
        db_session.add(bar(security))
        await db_session.flush()

        db_session.add(bar(security, close=Decimal("999.00"), high=Decimal("999.00")))
        with pytest.raises(IntegrityError):
            await db_session.flush()

    @pytest.mark.parametrize(
        ("overrides", "constraint"),
        [
            ({"high": Decimal("440.00")}, "high_is_not_below_low"),
            ({"high": Decimal("446.50")}, "high_is_the_highest"),
            ({"low": Decimal("446.90")}, "low_is_the_lowest"),
            # A nil *low* with a positive open and close satisfies every ordering check, so
            # this case is what forces the positivity constraint to cover all four prices
            # rather than only the traded ends.
            ({"low": Decimal("0")}, "prices_are_positive"),
            ({"volume": -1}, "volume_is_not_negative"),
        ],
    )
    async def test_an_impossible_bar_is_refused(self, db_session, security, overrides, constraint):
        db_session.add(bar(security, **overrides))

        with pytest.raises(IntegrityError) as excinfo:
            await db_session.flush()
        assert constraint in str(excinfo.value)

    async def test_the_vendor_adjusted_close_is_optional_and_kept_apart(self, db_session, security):
        """Stored as a cross-check, never as the answer.

        This platform computes its own adjustment from the recorded actions; where the two
        disagree that is worth surfacing, and it cannot be surfaced if only one is stored.
        """
        db_session.add(bar(security, adjusted_close=Decimal("445.10")))
        await db_session.flush()

        stored = await db_session.scalar(select(PriceBar))
        assert stored is not None
        assert stored.close != stored.adjusted_close


# -- The actions -------------------------------------------------------------------------


class TestACorporateActionIsOneKindOrTheOther:
    async def test_a_split_without_a_ratio_is_refused(self, db_session, security):
        db_session.add(
            CorporateAction(
                security_id=security.id,
                kind=CorporateActionKind.SPLIT,
                ex_date=date(2024, 5, 1),
            )
        )
        with pytest.raises(IntegrityError) as excinfo:
            await db_session.flush()
        assert "matches_its_kind" in str(excinfo.value)

    async def test_a_dividend_carrying_a_split_ratio_is_refused(self, db_session, security):
        db_session.add(
            CorporateAction(
                security_id=security.id,
                kind=CorporateActionKind.DIVIDEND,
                ex_date=date(2024, 5, 1),
                dividend_amount=Decimal("0.75"),
                dividend_currency="USD",
                split_ratio=Decimal("2"),
            )
        )
        with pytest.raises(IntegrityError) as excinfo:
            await db_session.flush()
        assert "matches_its_kind" in str(excinfo.value)

    async def test_a_dividend_must_say_what_currency_it_is_in(self, db_session, security):
        """A London listing quoted in pence can pay a dollar dividend. Ordinary, and a trap."""
        db_session.add(
            CorporateAction(
                security_id=security.id,
                kind=CorporateActionKind.DIVIDEND,
                ex_date=date(2024, 5, 1),
                dividend_amount=Decimal("0.75"),
            )
        )
        with pytest.raises(IntegrityError) as excinfo:
            await db_session.flush()
        assert "states_its_currency" in str(excinfo.value)

    async def test_a_nil_split_ratio_is_refused(self, db_session, security):
        db_session.add(
            CorporateAction(
                security_id=security.id,
                kind=CorporateActionKind.SPLIT,
                ex_date=date(2024, 5, 1),
                split_ratio=Decimal("0"),
            )
        )
        with pytest.raises(IntegrityError) as excinfo:
            await db_session.flush()
        assert "split_is_positive" in str(excinfo.value)

    async def test_a_nil_dividend_is_refused(self, db_session, security):
        """Nil passes the kind check — it is not null — so it needs a check of its own."""
        db_session.add(
            CorporateAction(
                security_id=security.id,
                kind=CorporateActionKind.DIVIDEND,
                ex_date=date(2024, 5, 1),
                dividend_amount=Decimal("0"),
                dividend_currency="USD",
            )
        )
        with pytest.raises(IntegrityError) as excinfo:
            await db_session.flush()
        assert "dividend_is_positive" in str(excinfo.value)

    async def test_a_consolidation_is_a_split_with_a_ratio_below_one(self, db_session, security):
        """A one-for-ten consolidation multiplies the share count by 0.1. Still a split."""
        db_session.add(
            CorporateAction(
                security_id=security.id,
                kind=CorporateActionKind.SPLIT,
                ex_date=date(2024, 5, 1),
                split_ratio=Decimal("0.1"),
            )
        )
        await db_session.flush()

        stored = await db_session.scalar(select(CorporateAction))
        assert stored is not None
        assert stored.split_ratio == Decimal("0.1000000000")


class TestTheTwoKindsAreUniqueDifferently:
    """The reason there are two partial indexes rather than one whole one."""

    async def test_a_security_cannot_split_twice_on_one_ex_date(self, db_session, security):
        for ratio in ("2", "3"):
            db_session.add(
                CorporateAction(
                    security_id=security.id,
                    kind=CorporateActionKind.SPLIT,
                    ex_date=date(2024, 5, 1),
                    split_ratio=Decimal(ratio),
                )
            )

        with pytest.raises(IntegrityError) as excinfo:
            await db_session.flush()
        assert "uq_corporate_actions_split" in str(excinfo.value)

    async def test_an_ordinary_and_a_special_dividend_may_share_an_ex_date(
        self, db_session, security
    ):
        """Real, and a single constraint over (security, kind, ex_date) would reject it."""
        for amount in ("0.75", "2.50"):
            db_session.add(
                CorporateAction(
                    security_id=security.id,
                    kind=CorporateActionKind.DIVIDEND,
                    ex_date=date(2024, 5, 1),
                    dividend_amount=Decimal(amount),
                    dividend_currency="USD",
                )
            )
        await db_session.flush()

        rows = list(await db_session.scalars(select(CorporateAction)))
        assert len(rows) == 2

    async def test_the_same_dividend_twice_is_still_refused(self, db_session, security):
        """Two identical amounts on one ex-date is a duplicated import, not a second payment."""
        for _ in range(2):
            db_session.add(
                CorporateAction(
                    security_id=security.id,
                    kind=CorporateActionKind.DIVIDEND,
                    ex_date=date(2024, 5, 1),
                    dividend_amount=Decimal("0.75"),
                    dividend_currency="USD",
                )
            )

        with pytest.raises(IntegrityError) as excinfo:
            await db_session.flush()
        assert "uq_corporate_actions_dividend" in str(excinfo.value)

    async def test_a_split_and_a_dividend_may_share_an_ex_date(self, db_session, security):
        """They do, routinely. The partial indexes do not see each other."""
        db_session.add(
            CorporateAction(
                security_id=security.id,
                kind=CorporateActionKind.SPLIT,
                ex_date=date(2024, 5, 1),
                split_ratio=Decimal("2"),
            )
        )
        db_session.add(
            CorporateAction(
                security_id=security.id,
                kind=CorporateActionKind.DIVIDEND,
                ex_date=date(2024, 5, 1),
                dividend_amount=Decimal("0.75"),
                dividend_currency="USD",
            )
        )
        await db_session.flush()

        rows = list(await db_session.scalars(select(CorporateAction)))
        assert len(rows) == 2


# -- What survives what ----------------------------------------------------------------------


class TestDeletionBehaviour:
    """Core deletes rather than ORM ones, so the database's rule is what is under test and
    not SQLAlchemy's in-memory cascade handling, which would answer for itself."""

    async def test_deleting_a_company_leaves_its_price_history(self, db_session, security):
        """A report already written cited those prices; the listing outlives the company row."""
        company = Company(
            name="Microsoft Corporation", cik="0000789019", ticker="MSFT", exchange="NASDAQ"
        )
        db_session.add(company)
        await db_session.flush()
        security.company_id = company.id
        db_session.add(bar(security))
        await db_session.flush()

        await db_session.execute(delete(Company).where(Company.id == company.id))
        db_session.expunge_all()

        survivor = await db_session.scalar(select(Security).where(Security.id == security.id))
        assert survivor is not None
        assert survivor.company_id is None
        assert await db_session.scalar(select(func.count()).select_from(PriceBar)) == 1

    async def test_deleting_a_security_takes_its_bars_and_actions(self, db_session, security):
        """The opposite choice, and deliberate: a bar with no listing means nothing."""
        db_session.add(bar(security))
        db_session.add(
            CorporateAction(
                security_id=security.id,
                kind=CorporateActionKind.SPLIT,
                ex_date=date(2024, 5, 1),
                split_ratio=Decimal("2"),
            )
        )
        await db_session.flush()

        await db_session.execute(delete(Security).where(Security.id == security.id))
        db_session.expunge_all()

        assert await db_session.scalar(select(func.count()).select_from(PriceBar)) == 0
        assert await db_session.scalar(select(func.count()).select_from(CorporateAction)) == 0

    async def test_losing_the_source_document_leaves_the_bar(self, db_session, security, scene):
        """ADR 0031: a purge takes the bytes and nothing else.

        A purge does not delete the `source_documents` row at all, so this is stronger than
        the case that actually arises — even destroying the provenance row outright leaves the
        price series, because a figure a report already cited must not vanish underneath it.
        """
        document = await provenance(db_session, scene)
        db_session.add(bar(security, source_document_id=document.id))
        await db_session.flush()

        await db_session.execute(delete(SourceDocument).where(SourceDocument.id == document.id))
        db_session.expunge_all()

        stored = await db_session.scalar(select(PriceBar))
        assert stored is not None
        assert stored.source_document_id is None
