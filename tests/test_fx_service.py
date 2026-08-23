"""Rates with somewhere to live, and `aer.calc.fx` with its first caller.

That module has shipped complete twice — under ADR 0026 with no source at all, under ADR
0045 with a source and nowhere to put what it returned — and nothing in `src/` has ever
called it. So the tests worth writing here are not about arithmetic, which
`tests/test_fx.py` and `tests/test_fx_source.py` already cover from both ends. They are
about the seam: whether a rate that went into the database comes back as the same rate, and
whether the refusals `select_rate` makes survive the round trip.

**Three of them would each be invisible in the output.** A pair with no observation on the
as-of date reported as missing rather than as unpublished-yet, sending somebody to re-run an
acquisition that already ran. A gap in a series silently bridged by reaching further back,
which converts a whole balance sheet at another week's rate. And a correction applied as an
update, which rewrites an input to arithmetic that has already been approved. Each has a
class below.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime
from decimal import Decimal, localcontext
from pathlib import Path
from typing import Any, Final

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from aer.calc.engine import CalculationContext
from aer.calc.fx import (
    LookAheadRateError,
    NoRateAvailableError,
    StaleRateError,
    round_trips,
)
from aer.calc.units import (
    CALC_CONTEXT,
    CalculationError,
    Quantity,
    SourceKind,
    SourceRef,
    SourceTable,
    Unit,
    money,
)
from aer.core.enums import JobStatus, Provider, SourceTier, UserRole
from aer.db.models import Artefact, FxRateRow, Job, SourceDocument, User, WorkOrder
from aer.fetch.client import FetchResult
from aer.services import calculations as calculation_service
from aer.services import fx as fx_service
from aer.sources.macro import ecb
from aer.sources.macro.client import ReferenceRateResponse

pytestmark = pytest.mark.integration

FIXTURES: Final = Path(__file__).parent / "fixtures" / "macro"

# The last day both fixture series publish on before the weekend, and the day almost every
# test below is as at. 2024-06-29 and 30 are blank in both files, which is what makes the
# reach-back and staleness cases real rather than constructed.
FRIDAY: Final = date(2024, 6, 28)
MONDAY: Final = date(2024, 7, 1)

USD_ON_FRIDAY: Final = Decimal("1.0705")
GBP_ON_FRIDAY: Final = Decimal("0.84645")

# What Friday's two legs make, computed the way the kernel computes it. Python's default
# decimal context carries 28 significant figures and `aer.calc` carries 34 (IEEE 754
# decimal128), so an expected value worked out the obvious way differs from the real one in
# the last six places — a failure that reads as a bug in the arithmetic and is a bug in the
# test.
with localcontext(CALC_CONTEXT):
    FRIDAY_CROSS: Final = GBP_ON_FRIDAY / USD_ON_FRIDAY
    A_THOUSAND_DOLLARS_IN_POUNDS: Final = Decimal("1000") * FRIDAY_CROSS


# Where a converted amount came from. Any sourced figure would do — what matters is that
# it has one: `@traced` refuses an input with no provenance, which is the platform declining
# to produce a number nobody could defend, and a test that passed an unsourced amount would
# be testing a path production cannot reach.
HOLDING: Final = SourceRef.financial_fact(
    "22222222-2222-2222-2222-222222222222", label="cash at bank"
)


def dollars(value: str) -> Quantity:
    return money(Decimal(value), "USD", source=HOLDING)


def payload(currency: str) -> bytes:
    return (FIXTURES / f"ecb_eurofxref_{currency.lower()}.csv").read_bytes()


def digest(currency: str) -> str:
    """The real hash of the fixture's bytes.

    Not ``"a" * 64``. The stored digest is meant to name the response a rate was parsed
    from, and a test that stored a constant would pass just as happily if the service wrote
    a constant — which is the one thing this column must not do.
    """
    return hashlib.sha256(payload(currency)).hexdigest()


def fetched(currency: str) -> FetchResult:
    """The fetch a response carries. These tests store rows; they do not fetch."""
    return FetchResult(
        url=f"https://data-api.ecb.europa.eu/service/data/EXR/D.{currency}.EUR.SP00.A",
        final_url=f"https://data-api.ecb.europa.eu/service/data/EXR/D.{currency}.EUR.SP00.A",
        status_code=200,
        sha256=digest(currency),
        size_bytes=len(payload(currency)),
        media_type="text/csv",
        declared_media_type="text/csv",
        headers={},
        redirect_chain=(),
        elapsed_ms=1.0,
        attempts=1,
    )


def published(currency: str, *, as_of: date = MONDAY) -> ReferenceRateResponse:
    """One currency's series as the client would return it."""
    return ReferenceRateResponse(
        rates=ecb.parse_reference_rates(payload(currency), currency=currency),
        as_of=as_of,
        fetch=fetched(currency),
    )


def corrected(currency: str, *, on: date, to: str, as_of: date) -> ReferenceRateResponse:
    """The same series with one day's figure restated, read at a later vintage."""
    original = published(currency, as_of=as_of).rates
    observations = tuple(
        (day, Decimal(to) if day == on else value) for day, value in original.observations
    )
    return ReferenceRateResponse(
        rates=ecb.ReferenceRates(currency=original.currency, observations=observations),
        as_of=as_of,
        fetch=fetched(currency),
    )


@pytest.fixture
async def document(db_session: Any) -> SourceDocument:
    """The archived ECB response every stored rate points at."""
    user = User(email="rates@example.invalid", display_name="R", role=UserRole.OWNER)
    artefact = Artefact(sha256="e" * 64, size_bytes=512, media_type="text/csv", storage_key="ee/e")
    db_session.add_all([user, artefact])
    await db_session.flush()

    order = WorkOrder(user_id=user.id, as_of_date=MONDAY, point_in_time=True)
    db_session.add(order)
    await db_session.flush()

    row = SourceDocument(
        work_order_id=order.id,
        artefact_id=artefact.id,
        url="https://data-api.ecb.europa.eu/service/data/EXR/D.USD.EUR.SP00.A?format=csvdata",
        provider=Provider.ECB,
        source_tier=SourceTier.T3_OFFICIAL_STATS,
        title="ECB euro foreign-exchange reference rates",
        retrieved_at=datetime.now(UTC),
    )
    db_session.add(row)
    await db_session.flush()
    return row


@pytest.fixture
async def stored(db_session: Any, document: SourceDocument) -> SourceDocument:
    """Both fixture series recorded, which is the state every read test starts from."""
    for currency in ("USD", "GBP"):
        await fx_service.record_reference_rates(
            db_session, published(currency), source_document_id=document.id
        )
    return document


@pytest.fixture
def context() -> CalculationContext:
    return CalculationContext(code_version="test")


async def rows_for(session: Any, currency: str) -> list[FxRateRow]:
    return list(
        await session.scalars(
            select(FxRateRow)
            .where(FxRateRow.quote == currency)
            .order_by(FxRateRow.observed_on, FxRateRow.vintage)
        )
    )


class TestStoringWhatWasPublished:
    async def test_a_blank_day_is_not_a_rate_of_zero(self, db_session, document) -> None:
        # The two weekend rows in the fixture carry no value. Six figures, eight rows in
        # the file — and a zero in either gap would be an infinite conversion.
        result = await fx_service.record_reference_rates(
            db_session, published("USD"), source_document_id=document.id
        )

        assert result.inserted == 6
        assert {row.observed_on for row in await rows_for(db_session, "USD")} == {
            date(2024, 6, 24),
            date(2024, 6, 25),
            date(2024, 6, 26),
            date(2024, 6, 27),
            FRIDAY,
            MONDAY,
        }

    async def test_every_row_is_the_euro_against_something(self, db_session, stored) -> None:
        # A property of the source rather than a choice: the ECB publishes the rates *of*
        # the euro. A row with anything else on the left would be a cross somebody stored
        # as though it had been published.
        assert {row.base for row in await rows_for(db_session, "GBP")} == {"EUR"}

    async def test_a_retry_writes_no_second_copy(self, db_session, document) -> None:
        await fx_service.record_reference_rates(
            db_session, published("USD"), source_document_id=document.id
        )

        again = await fx_service.record_reference_rates(
            db_session, published("USD"), source_document_id=document.id
        )

        assert (again.inserted, again.already_held) == (0, 6)
        assert len(await rows_for(db_session, "USD")) == 6

    async def test_an_observation_after_the_as_of_date_never_reaches_the_table(
        self, db_session, document
    ) -> None:
        """Invariant 4 at acquisition, which is where it is meant to be enforced.

        The URL is already bounded by the as-of date, so this only fires if the portal
        answers with more than it was asked for. That is exactly the case a control living
        in a query parameter would not survive.
        """
        result = await fx_service.record_reference_rates(
            db_session, published("USD", as_of=FRIDAY), source_document_id=document.id
        )

        assert (result.inserted, result.refused_after_as_of) == (5, 1)
        assert MONDAY not in {row.observed_on for row in await rows_for(db_session, "USD")}

    async def test_every_row_names_the_bytes_it_was_parsed_from(self, db_session, document) -> None:
        # The real digest of the fixture, so this fails if the service ever writes a
        # constant, a placeholder, or the hash of the wrong response.
        await fx_service.record_reference_rates(
            db_session, published("USD"), source_document_id=document.id
        )

        assert {row.artefact_sha256 for row in await rows_for(db_session, "USD")} == {digest("USD")}

    async def test_a_rate_nobody_published_is_not_a_row_here(self, db_session, document) -> None:
        """The door ADR 0082 closed, in the column ADR 0084 moved it to.

        A rate a person typed has no response behind it and therefore no digest to give.
        The pointer to the document is nullable now — a purge nulls it — so the hash is
        what the schema demands, and it demands it in a form nobody can invent.
        """
        db_session.add(
            FxRateRow(
                base="EUR",
                quote="USD",
                observed_on=FRIDAY,
                vintage=FRIDAY,
                rate=USD_ON_FRIDAY,
                source_document_id=document.id,
            )
        )

        # Matched on the column, so this cannot start passing because some *other*
        # constraint fired — a test that raises for the wrong reason is a test that stops
        # checking the thing it was written for.
        with pytest.raises(IntegrityError, match="artefact_sha256"):
            await db_session.flush()


class TestARateOutlivesTheRequestThatFetchedIt:
    """ADR 0084, which exists because ADR 0082's column met the purge and lost.

    The euro's rate for a Friday is not about whichever company happened to be under
    research when it was fetched. The portfolio needs it every day the book is open, a
    second request needs the same row, and a published report's lineage cites it. A
    ``NOT NULL RESTRICT`` on a request-scoped document made that request unpurgeable
    forever, with no recourse an operator could take that was not deleting evidence.
    """

    async def test_the_rate_survives_its_document_with_the_digest_intact(
        self, db_session, stored
    ) -> None:
        await db_session.execute(delete(SourceDocument).where(SourceDocument.id == stored.id))
        await db_session.flush()

        surviving = await rows_for(db_session, "USD")

        assert len(surviving) == 6
        assert {row.source_document_id for row in surviving} == {None}
        # "Show me that response" is gone and "what were these numbers taken from" is not,
        # which is the same trade `price_bars` makes under ADR 0031.
        assert {row.artefact_sha256 for row in surviving} == {digest("USD")}


class TestACorrectionIsANewRowNotAnUpdate:
    """What stops a restatement rewriting arithmetic that has already been approved."""

    async def test_the_restated_figure_arrives_beside_the_original(
        self, db_session, document
    ) -> None:
        await fx_service.record_reference_rates(
            db_session, published("USD", as_of=FRIDAY), source_document_id=document.id
        )

        await fx_service.record_reference_rates(
            db_session,
            corrected("USD", on=FRIDAY, to="1.0800", as_of=MONDAY),
            source_document_id=document.id,
        )

        for_friday = [row for row in await rows_for(db_session, "USD") if row.observed_on == FRIDAY]
        assert [(row.vintage, row.rate) for row in for_friday] == [
            (FRIDAY, USD_ON_FRIDAY),
            (MONDAY, Decimal("1.0800")),
        ]

    async def test_the_newest_reading_is_the_one_a_conversion_uses(
        self, db_session, document, context
    ) -> None:
        await fx_service.record_reference_rates(
            db_session, published("USD", as_of=FRIDAY), source_document_id=document.id
        )
        await fx_service.record_reference_rates(
            db_session,
            corrected("USD", on=FRIDAY, to="1.0800", as_of=MONDAY),
            source_document_id=document.id,
        )

        rate = await fx_service.rate_as_at(
            db_session, context, base="EUR", quote="USD", as_of=FRIDAY
        )

        assert rate.rate.value == Decimal("1.0800")


class TestReadingBackWhatADateCouldHaveUsed:
    async def test_the_published_direction_is_read_straight_off_the_table(
        self, db_session, stored, context
    ) -> None:
        rate = await fx_service.rate_as_at(
            db_session, context, base="EUR", quote="USD", as_of=FRIDAY
        )

        assert rate.rate.value == USD_ON_FRIDAY
        assert rate.rate.unit == Unit.parse("USD/EUR")
        assert rate.observed_on == FRIDAY
        # A fact in `fx_rates`, not a fact in `financial_facts`: the kind is the guarantee
        # and the table is the relation, and ADR 0076 is about not conflating them.
        assert rate.source.kind is SourceKind.FACT
        assert rate.source.table is SourceTable.FX_RATES
        assert not context.records, "reading a published rate is not a calculation"

    async def test_a_hundred_euros_buys_more_dollars_and_fewer_pounds(
        self, db_session, stored, context
    ) -> None:
        """Which way the rate points, asked the only way that would notice it flipped.

        Nothing downstream checks this. `FxRate` refuses a rate whose unit disagrees with
        its pair, but the unit is derived from the row's own base and quote — so a row
        written backwards agrees with itself and every guard passes. What catches it is
        converting a known amount and looking at which way it moved: €100 is $107.05 and
        £84.65, and a flipped rate would make it $93.41 without any of the arithmetic
        complaining.
        """
        euros = money(Decimal("100"), "EUR", source=HOLDING)

        dollars_out = await fx_service.convert_as_at(
            db_session, context, amount=euros, into="USD", as_of=FRIDAY
        )
        pounds_out = await fx_service.convert_as_at(
            db_session, context, amount=euros, into="GBP", as_of=FRIDAY
        )

        assert dollars_out.value == Decimal("100") * USD_ON_FRIDAY
        assert pounds_out.value == Decimal("100") * GBP_ON_FRIDAY

    async def test_the_other_direction_is_one_observation_read_backwards(
        self, db_session, stored, context
    ) -> None:
        """An inverted rate keeps the original's source, and that is not a detail.

        A USD/EUR rate derived from the EUR/USD one is the same published figure. If it
        minted its own reference, a round trip would trace to two pieces of evidence where
        there is only ever one.
        """
        published_rate = await fx_service.rate_as_at(
            db_session, context, base="EUR", quote="USD", as_of=FRIDAY
        )
        inverted = await fx_service.rate_as_at(
            db_session, context, base="USD", quote="EUR", as_of=FRIDAY
        )

        assert inverted.rate.unit == Unit.parse("EUR/USD")
        assert inverted.source.identifier == published_rate.source.identifier
        assert not context.records

    async def test_a_pair_the_source_does_not_publish_is_a_recorded_calculation(
        self, db_session, stored, context
    ) -> None:
        """There is no GBP/USD reference rate and there never will be.

        So one is a division of two published legs, and it is traced — because a derived
        rate that looked published would be this platform asserting something nobody
        stated.
        """
        rate = await fx_service.rate_as_at(
            db_session, context, base="USD", quote="GBP", as_of=FRIDAY
        )

        assert rate.rate.unit == Unit.parse("GBP/USD")
        assert rate.rate.value == FRIDAY_CROSS
        assert [record.name for record in context.records] == ["fx_cross_rate"]
        assert rate.source.kind is SourceKind.CALCULATION

    async def test_a_weekend_reaches_back_to_the_last_day_that_published(
        self, db_session, stored, context
    ) -> None:
        # A run as at a Saturday must not fail for a reason that has nothing to do with the
        # data: published series skip weekends, and the currency did not stop moving.
        rate = await fx_service.rate_as_at(
            db_session, context, base="EUR", quote="USD", as_of=date(2024, 6, 29)
        )

        assert rate.observed_on == FRIDAY

    async def test_a_gap_wider_than_the_window_refuses_rather_than_reaching_further(
        self, db_session, stored, context
    ) -> None:
        # Nine days past the last observation. A hole in the series, not a currency that
        # stopped moving — and a run that stops is recoverable where a balance sheet
        # converted at another week's rate is not.
        with pytest.raises(StaleRateError):
            await fx_service.rate_as_at(
                db_session, context, base="EUR", quote="USD", as_of=date(2024, 7, 10)
            )

    async def test_a_pair_published_only_later_says_so_rather_than_looking_missing(
        self, db_session, stored, context
    ) -> None:
        """The distinction the candidate query goes out of its way to keep.

        "Every observation is later than the as-of date" and "there is no such pair" are
        different faults with different fixes, and the second sends somebody looking for an
        acquisition that already ran. Filtering the as-of date in SQL would collapse them.
        """
        with pytest.raises(LookAheadRateError):
            await fx_service.rate_as_at(
                db_session, context, base="EUR", quote="USD", as_of=date(2024, 6, 1)
            )

    async def test_a_pair_nobody_ever_stored_is_not_a_stale_one(
        self, db_session, stored, context
    ) -> None:
        with pytest.raises(NoRateAvailableError) as raised:
            await fx_service.rate_as_at(db_session, context, base="EUR", quote="CHF", as_of=FRIDAY)

        assert not isinstance(raised.value, StaleRateError | LookAheadRateError)

    async def test_a_currency_converted_to_itself_is_refused(
        self, db_session, stored, context
    ) -> None:
        # Not one. A pair of identical currencies reaching a rate lookup is a caller that
        # did not notice, and returning unity would let it go on not noticing.
        with pytest.raises(CalculationError, match="asking for the number one"):
            await fx_service.rate_as_at(db_session, context, base="USD", quote="USD", as_of=FRIDAY)

    async def test_something_that_is_not_a_currency_code_is_refused(
        self, db_session, stored, context
    ) -> None:
        with pytest.raises(CalculationError):
            await fx_service.rate_as_at(
                db_session, context, base="EUR", quote="dollars", as_of=FRIDAY
            )


class TestConverting:
    async def test_a_dollar_amount_becomes_pounds_through_two_recorded_steps(
        self, db_session, stored, context
    ) -> None:
        converted = await fx_service.convert_as_at(
            db_session, context, amount=dollars("1000"), into="GBP", as_of=FRIDAY
        )

        assert converted.unit == Unit.currency("GBP")
        assert converted.value == A_THOUSAND_DOLLARS_IN_POUNDS
        # The cross first, then the conversion that applied it. Two rows, so a reader
        # following the figure back reaches the two published legs and the formula.
        assert [record.name for record in context.records] == ["fx_cross_rate", "fx_convert"]

    async def test_the_currency_is_read_off_the_amount_rather_than_asked_for(
        self, db_session, stored, context
    ) -> None:
        # A caller that could name the source currency could name the wrong one, and the
        # unit check downstream would have nothing to attribute the mismatch to.
        with pytest.raises(CalculationError, match="not an amount in one currency"):
            await fx_service.convert_as_at(
                db_session,
                context,
                amount=Quantity.of(Decimal("0.4"), Unit.parse("pure")),
                into="GBP",
                as_of=FRIDAY,
            )

    async def test_out_and_back_returns_what_it_started_with(
        self, db_session, stored, context
    ) -> None:
        original = dollars("1000")

        pounds = await fx_service.convert_as_at(
            db_session, context, amount=original, into="GBP", as_of=FRIDAY
        )
        returned = await fx_service.convert_as_at(
            db_session, context, amount=pounds, into="USD", as_of=FRIDAY
        )

        assert round_trips(original, returned)


class TestTheRateIsALeafSomebodyCanWalkTo:
    """ADR 0076's registry, paying off for the relation it was built before.

    A converted figure is only as auditable as its rate, and a rate whose lineage node says
    "0.79" without saying which pair or which day is a number a reader cannot check. 0.79 is
    a plausible GBP/USD rate and an impossible USD/GBP one.
    """

    async def test_a_converted_figure_traces_to_the_pair_and_the_day(
        self, db_session, stored, context
    ) -> None:
        user = await db_session.scalar(select(User))
        order = await db_session.scalar(select(WorkOrder))
        job = Job(
            work_order_id=order.id,
            workflow_version="test",
            code_version="test",
            status=JobStatus.RUNNING,
            started_at=datetime.now(UTC),
        )
        db_session.add(job)
        await db_session.flush()
        assert user is not None

        await fx_service.convert_as_at(
            db_session, context, amount=dollars("1000"), into="GBP", as_of=FRIDAY
        )
        rows = await calculation_service.persist_context(db_session, context, job_id=job.id)

        walked = (await calculation_service.lineage(db_session, rows[-1].id)).as_dict()
        legs = [
            node
            for node in walked["inputs"][-1]["inputs"]
            if node["detail"].get("table") == SourceTable.FX_RATES.value
        ]

        assert len(legs) == 2
        assert {(leg["detail"]["base"], leg["detail"]["quote"]) for leg in legs} == {
            ("EUR", "USD"),
            ("EUR", "GBP"),
        }
        assert {leg["detail"]["observed_on"] for leg in legs} == {FRIDAY.isoformat()}
        assert all(leg["detail"]["source_document_id"] == str(stored.id) for leg in legs)
        # And the half of that answer a purge would leave behind.
        assert {leg["detail"]["artefact_sha256"] for leg in legs} == {
            digest("USD"),
            digest("GBP"),
        }
