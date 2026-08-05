"""Storing vintages, and reading back the one an as-of date actually had.

`TestTwoRunsWithDifferentAsOfDates` is task 25's acceptance criterion against the database:
two runs over the same series with different as-of dates get different values, and both trace
to a vintage. Everything else here is a way that could be lost — a fallback to the current
figure, a period that had not happened yet, or a retry writing a second copy and making a
revision appear where none happened.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from aer.calc.engine import CalculationContext
from aer.calc.units import CalculationError, Unit
from aer.core.enums import Provider
from aer.db.models import MacroObservationRow, MacroSeriesRow
from aer.services import macro as macro_service
from aer.sources.macro.client import MacroResponse
from aer.sources.macro.fred import MacroObservation
from aer.sources.macro.series import SeriesRefusedError, series_for

pytestmark = pytest.mark.integration

GDP = series_for("us_gdp_nominal")
TEN_YEAR = series_for("us_treasury_10y")
UK_CPI = series_for("uk_cpi")


@pytest.fixture
async def clean(db_session: Any) -> None:
    await db_session.execute(text("TRUNCATE macro_series RESTART IDENTITY CASCADE"))


def response(
    series: Any, *, vintage: date, values: dict[date, str], is_archived: bool = True
) -> MacroResponse:
    """A retrieved series, without the fetch. The fetch has its own tests."""
    return MacroResponse(
        series=series,
        vintage=vintage,
        observations=tuple(
            MacroObservation(observed_on=period, vintage=vintage, value=Decimal(value))
            for period, value in sorted(values.items())
        ),
        is_archived=is_archived,
        fetch=None,  # type: ignore[arg-type]  # not read by the service
    )


# The two vintages of US GDP the parser tests use, as they would be stored.
EARLY = response(
    GDP,
    vintage=date(2020, 6, 30),
    values={
        date(2019, 10, 1): "21747.394",
        date(2020, 1, 1): "21561.139",
    },
)
LATE = response(
    GDP,
    vintage=date(2024, 6, 30),
    values={
        date(2019, 10, 1): "21902.390",
        date(2020, 1, 1): "21727.657",
        date(2020, 4, 1): "19935.444",
    },
)


class TestTwoRunsWithDifferentAsOfDates:
    """Task 25's acceptance criterion, against the database."""

    async def test_they_get_different_values_for_the_same_period(self, db_session, clean):
        await macro_service.record_series(db_session, EARLY)
        await macro_service.record_series(db_session, LATE)

        in_2020 = await macro_service.observation_as_at(
            db_session, key="us_gdp_nominal", as_of=date(2020, 6, 30)
        )
        in_2024 = await macro_service.observation_as_at(
            db_session, key="us_gdp_nominal", as_of=date(2024, 6, 30)
        )

        assert in_2020.value == Decimal("21561.139")
        assert in_2024.value == Decimal("19935.444")

    async def test_both_are_traceable_to_a_vintage(self, db_session, clean):
        await macro_service.record_series(db_session, EARLY)
        await macro_service.record_series(db_session, LATE)

        in_2020 = await macro_service.observation_as_at(
            db_session, key="us_gdp_nominal", as_of=date(2020, 6, 30)
        )
        in_2024 = await macro_service.observation_as_at(
            db_session, key="us_gdp_nominal", as_of=date(2024, 6, 30)
        )

        assert in_2020.vintage == date(2020, 6, 30)
        assert in_2024.vintage == date(2024, 6, 30)

    async def test_the_earlier_run_does_not_see_the_revision(self, db_session, clean):
        """The whole point. The 2024 figure for Q1 2020 exists and must not be reachable."""
        await macro_service.record_series(db_session, EARLY)
        await macro_service.record_series(db_session, LATE)

        found = await macro_service.observation_as_at(
            db_session, key="us_gdp_nominal", as_of=date(2020, 6, 30)
        )
        assert found.value != Decimal("21727.657")

    async def test_the_earlier_run_does_not_see_a_period_that_had_not_happened(
        self, db_session, clean
    ):
        """Q2 2020 was not published in June 2020, and its vintage says so."""
        await macro_service.record_series(db_session, EARLY)
        await macro_service.record_series(db_session, LATE)

        found = await macro_service.observation_as_at(
            db_session, key="us_gdp_nominal", as_of=date(2020, 6, 30)
        )
        assert found.observed_on == date(2020, 1, 1)


class TestNothingFallsBackToTheCurrentSeries:
    async def test_an_as_of_date_before_every_vintage_returns_nothing(self, db_session, clean):
        """Not the oldest row it can find. `None` is the honest answer."""
        await macro_service.record_series(db_session, LATE)

        found = await macro_service.observation_as_at(
            db_session, key="us_gdp_nominal", as_of=date(2021, 1, 1)
        )
        assert found is None

    async def test_a_period_after_the_as_of_date_is_not_returned(self, db_session, clean):
        """Published by then, and describing a period that had happened. Both, not either."""
        await macro_service.record_series(
            db_session,
            response(GDP, vintage=date(2024, 6, 30), values={date(2024, 4, 1): "1.0"}),
        )

        found = await macro_service.observation_as_at(
            db_session, key="us_gdp_nominal", as_of=date(2024, 3, 31)
        )
        assert found is None

    async def test_the_newest_vintage_not_after_the_cutoff_wins(self, db_session, clean):
        """Three vintages of one period; the one in the middle is the right answer."""
        for vintage, value in (
            (date(2020, 4, 30), "1.0"),
            (date(2020, 7, 31), "2.0"),
            (date(2021, 1, 31), "3.0"),
        ):
            await macro_service.record_series(
                db_session,
                response(GDP, vintage=vintage, values={date(2020, 1, 1): value}),
            )

        found = await macro_service.observation_as_at(
            db_session, key="us_gdp_nominal", as_of=date(2020, 9, 30)
        )
        assert found.value == Decimal("2.0")
        assert found.vintage == date(2020, 7, 31)

    async def test_a_series_never_retrieved_returns_nothing(self, db_session, clean):
        found = await macro_service.observation_as_at(
            db_session, key="us_cpi", as_of=date(2024, 6, 30)
        )
        assert found is None

    async def test_a_refused_series_raises_rather_than_returning_nothing(self, db_session, clean):
        """ "Not allowlisted" and "not retrieved yet" are different answers."""
        with pytest.raises(SeriesRefusedError):
            await macro_service.observation_as_at(
                db_session, key="CSUSHPINSA", as_of=date(2024, 6, 30)
            )


class TestRecordingIsIdempotent:
    async def test_storing_the_same_vintage_twice_writes_once(self, db_session, clean):
        """A retried step must not make a revision appear where none happened."""
        first = await macro_service.record_series(db_session, EARLY)
        second = await macro_service.record_series(db_session, EARLY)

        assert first == 2
        assert second == 0

    async def test_a_second_vintage_adds_rows_rather_than_replacing(self, db_session, clean):
        await macro_service.record_series(db_session, EARLY)
        await macro_service.record_series(db_session, LATE)

        rows = list(await db_session.scalars(select(MacroObservationRow)))
        assert len(rows) == 5

    async def test_the_series_row_is_created_once(self, db_session, clean):
        await macro_service.record_series(db_session, EARLY)
        await macro_service.record_series(db_session, LATE)

        rows = list(await db_session.scalars(select(MacroSeriesRow)))
        assert len(rows) == 1
        assert rows[0].key == "us_gdp_nominal"


class TestTheSchemaEnforcesWhatTheQueryAssumes:
    """The check constraint the point-in-time read leans on.

    `observation_as_at` filters on both the vintage and the period, and the second filter is
    redundant *because of this constraint*: a period cannot postdate its own vintage, so a
    vintage at or before the as-of date cannot carry a later period. No test can distinguish
    that filter's presence from its absence while the constraint holds, so the constraint is
    what gets tested.
    """

    async def test_a_vintage_before_the_period_it_describes_is_refused(self, db_session, clean):
        """A figure published before the quarter it measures had happened is an import error."""
        row = await macro_service.upsert_series(db_session, GDP)
        db_session.add(
            MacroObservationRow(
                series_id=row.id,
                observed_on=date(2024, 1, 1),
                vintage=date(2023, 6, 30),
                value=Decimal("1.0"),
                is_archived=True,
            )
        )
        with pytest.raises(IntegrityError):
            await db_session.flush()

    async def test_a_vintage_equal_to_the_period_is_allowed(self, db_session, clean):
        """A daily yield is published the day it is for. The constraint must not refuse that."""
        row = await macro_service.upsert_series(db_session, TEN_YEAR)
        db_session.add(
            MacroObservationRow(
                series_id=row.id,
                observed_on=date(2024, 6, 28),
                vintage=date(2024, 6, 28),
                value=Decimal("4.36"),
                is_archived=True,
            )
        )
        await db_session.flush()

    async def test_one_period_at_one_vintage_cannot_be_stored_twice(self, db_session, clean):
        """Two rows would mean one archive answered twice with different figures."""
        await macro_service.record_series(db_session, EARLY)
        row = await db_session.scalar(select(MacroSeriesRow))

        db_session.add(
            MacroObservationRow(
                series_id=row.id,
                observed_on=date(2020, 1, 1),
                vintage=date(2020, 6, 30),
                value=Decimal("99999"),
                is_archived=True,
            )
        )
        with pytest.raises(IntegrityError):
            await db_session.flush()


class TestWhatTheSeriesRowCarries:
    async def test_it_records_who_produced_the_numbers(self, db_session, clean):
        """FRED distributes; the BEA produced. The copyright question is about the second."""
        await macro_service.record_series(db_session, EARLY)

        row = await db_session.scalar(select(MacroSeriesRow))
        assert row.originator == "US Bureau of Economic Analysis"
        assert row.provider is Provider.FRED

    async def test_it_copies_the_licence_rather_than_referencing_it(self, db_session, clean):
        """A registry edited next year must not rewrite attribution on work already done."""
        await macro_service.record_series(db_session, EARLY)

        row = await db_session.scalar(select(MacroSeriesRow))
        assert "public domain" in row.licence_note

    async def test_an_archived_vintage_is_marked_as_one(self, db_session, clean):
        await macro_service.record_series(db_session, EARLY)

        row = await db_session.scalar(select(MacroObservationRow))
        assert row.is_archived is True

    async def test_an_ons_release_is_not_marked_as_an_archive(self, db_session, clean):
        """A UK figure must not borrow a US figure's point-in-time guarantee."""
        await macro_service.record_series(
            db_session,
            response(
                UK_CPI,
                vintage=date(2024, 6, 19),
                values={date(2024, 5, 1): "131.5"},
                is_archived=False,
            ),
        )

        row = await db_session.scalar(select(MacroObservationRow))
        assert row.is_archived is False


class TestTheRiskFreeRate:
    async def test_a_usd_run_resolves_the_ten_year_treasury_at_its_vintage(self, db_session, clean):
        await macro_service.record_series(
            db_session,
            response(
                TEN_YEAR,
                vintage=date(2024, 6, 28),
                values={date(2024, 6, 27): "4.29", date(2024, 6, 28): "4.36"},
            ),
        )

        found = await macro_service.risk_free_rate_as_at(
            db_session, currency="USD", as_of=date(2024, 6, 28)
        )
        assert found.value == Decimal("4.36")

    async def test_it_does_not_reach_past_the_as_of_date(self, db_session, clean):
        """Two vintages of a daily series, a day apart. The earlier run sees only the earlier.

        Modelled the way the archive actually works: the 27 June yield appears in the 28 June
        vintage, so a run standing on 27 June reads the 27 June vintage and gets the 26th's
        figure. A test that put both days in one vintage would be asserting against a
        situation the archive never produces.
        """
        await macro_service.record_series(
            db_session,
            response(
                TEN_YEAR,
                vintage=date(2024, 6, 27),
                values={date(2024, 6, 25): "4.24", date(2024, 6, 26): "4.31"},
            ),
        )
        await macro_service.record_series(
            db_session,
            response(
                TEN_YEAR,
                vintage=date(2024, 6, 28),
                values={date(2024, 6, 27): "4.29", date(2024, 6, 28): "4.36"},
            ),
        )

        found = await macro_service.risk_free_rate_as_at(
            db_session, currency="USD", as_of=date(2024, 6, 27)
        )
        assert found.value == Decimal("4.31")
        assert found.vintage == date(2024, 6, 27)

    async def test_a_rate_published_the_next_day_is_not_reachable_today(self, db_session, clean):
        """The 27th's yield exists in the database and a 27th run must not see it."""
        await macro_service.record_series(
            db_session,
            response(
                TEN_YEAR,
                vintage=date(2024, 6, 28),
                values={date(2024, 6, 27): "4.29", date(2024, 6, 28): "4.36"},
            ),
        )

        found = await macro_service.risk_free_rate_as_at(
            db_session, currency="USD", as_of=date(2024, 6, 27)
        )
        assert found is None

    async def test_a_currency_with_no_documented_series_is_refused(self, db_session, clean):
        """Not defaulted to the US yield: that error is the whole rate differential."""
        with pytest.raises(SeriesRefusedError, match="rate differential"):
            await macro_service.risk_free_rate_as_at(
                db_session, currency="GBP", as_of=date(2024, 6, 28)
            )


class TestAnObservationBecomesASourcedQuantity:
    async def test_it_carries_the_series_unit(self, db_session, clean):
        await macro_service.record_series(
            db_session,
            response(TEN_YEAR, vintage=date(2024, 6, 28), values={date(2024, 6, 28): "4.36"}),
        )
        row = await macro_service.observation_as_at(
            db_session, key="us_treasury_10y", as_of=date(2024, 6, 28)
        )

        quantity = macro_service.as_quantity(row, series=TEN_YEAR)
        assert quantity.value == Decimal("4.36")
        assert quantity.unit == Unit.parse("pure")

    async def test_its_source_names_the_period_and_the_vintage(self, db_session, clean):
        """Two readings of one statistic are different facts, and the label says which."""
        await macro_service.record_series(
            db_session,
            response(TEN_YEAR, vintage=date(2024, 6, 28), values={date(2024, 6, 28): "4.36"}),
        )
        row = await macro_service.observation_as_at(
            db_session, key="us_treasury_10y", as_of=date(2024, 6, 28)
        )

        label = macro_service.as_quantity(row, series=TEN_YEAR).source.label
        assert "2024-06-28" in label
        assert "vintage" in label

    async def test_it_is_a_fact_rather_than_an_assumption(self, db_session, clean):
        """A published statistic is an observation somebody made, not a value somebody chose."""
        await macro_service.record_series(
            db_session,
            response(TEN_YEAR, vintage=date(2024, 6, 28), values={date(2024, 6, 28): "4.36"}),
        )
        row = await macro_service.observation_as_at(
            db_session, key="us_treasury_10y", as_of=date(2024, 6, 28)
        )

        assert macro_service.as_quantity(row, series=TEN_YEAR).source.kind == "fact"


class TestAVintageBecomesADiscountRateInput:
    """The stored figure is a percentage; the arithmetic wants a fraction. See ADR 0027."""

    async def test_a_published_yield_converts_to_the_fraction_capm_needs(self, db_session, clean):
        await macro_service.record_series(
            db_session,
            response(TEN_YEAR, vintage=date(2024, 6, 28), values={date(2024, 6, 28): "4.36"}),
        )
        row = await macro_service.observation_as_at(
            db_session, key="us_treasury_10y", as_of=date(2024, 6, 28)
        )
        context = CalculationContext(code_version="testsha")

        converted = macro_service.as_rate(row, series=TEN_YEAR, context=context)

        assert converted.value == Decimal("0.0436")
        assert row.value == Decimal("4.36"), "the stored fact must still match the source"

    async def test_the_conversion_is_a_step_in_the_ledger(self, db_session, clean):
        """Not an inline division somewhere. A reader can see it happened, and to what."""
        await macro_service.record_series(
            db_session,
            response(TEN_YEAR, vintage=date(2024, 6, 28), values={date(2024, 6, 28): "4.36"}),
        )
        row = await macro_service.observation_as_at(
            db_session, key="us_treasury_10y", as_of=date(2024, 6, 28)
        )
        context = CalculationContext(code_version="testsha")

        macro_service.as_rate(row, series=TEN_YEAR, context=context)

        (record,) = context.named("rate_from_percent")
        assert record.inputs[0].value == Decimal("4.36")
        assert "vintage" in record.inputs[0].source_label
        assert record.output_value == Decimal("0.0436")

    async def test_a_series_that_is_not_a_percentage_is_refused(self, db_session, clean):
        """An index level divided by a hundred is a plausible number from the wrong series."""
        await macro_service.record_series(
            db_session,
            response(UK_CPI, vintage=date(2024, 6, 19), values={date(2024, 5, 1): "131.5"}),
        )
        row = await macro_service.observation_as_at(
            db_session, key="uk_cpi", as_of=date(2024, 6, 19)
        )
        context = CalculationContext(code_version="testsha")

        with pytest.raises(CalculationError, match="not published as a percentage"):
            macro_service.as_rate(row, series=UK_CPI, context=context)


class TestReadingAWholeSeries:
    async def test_each_period_comes_back_at_its_newest_usable_vintage(self, db_session, clean):
        await macro_service.record_series(db_session, EARLY)
        await macro_service.record_series(db_session, LATE)

        rows = await macro_service.observations_for_series(
            db_session, key="us_gdp_nominal", as_of=date(2024, 6, 30)
        )

        assert [r.observed_on for r in rows] == [
            date(2019, 10, 1),
            date(2020, 1, 1),
            date(2020, 4, 1),
        ]
        assert all(r.vintage == date(2024, 6, 30) for r in rows)

    async def test_an_earlier_as_of_date_gives_the_earlier_readings(self, db_session, clean):
        await macro_service.record_series(db_session, EARLY)
        await macro_service.record_series(db_session, LATE)

        rows = await macro_service.observations_for_series(
            db_session, key="us_gdp_nominal", as_of=date(2020, 6, 30)
        )

        assert [r.value for r in rows] == [Decimal("21747.394"), Decimal("21561.139")]

    async def test_a_period_with_nothing_published_yet_is_absent_not_carried_forward(
        self, db_session, clean
    ):
        await macro_service.record_series(db_session, EARLY)
        await macro_service.record_series(db_session, LATE)

        rows = await macro_service.observations_for_series(
            db_session, key="us_gdp_nominal", as_of=date(2020, 6, 30)
        )
        assert date(2020, 4, 1) not in [r.observed_on for r in rows]
