"""The closing section (F3, ADR 0129): what a planned position would do to the operator's book.

Three layers, tested at each. The arithmetic in `aer.calc.consequences` is pure and holds
under Hypothesis; the block in `aer.sections.consequences` strikes it once from the book and
composes it from the ledger after that; and the document carries the section to the operator
in full and to a shared copy only where its lineage is documented throughout (ADR 0073).

**The book is the shared portfolio scene**: a sterling book, a US listing quoted in dollars
with one close, and the two ECB legs that join them. Every trade the scene enters is typed —
the attested grade — unless a test says otherwise, so the default consequence is one the
shared copy withholds, which is the containment the tests exist to prove.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.calc import consequences as calc
from aer.calc.comps import Audience
from aer.calc.engine import CalculationContext
from aer.calc.units import (
    DIMENSIONLESS,
    CalculationError,
    Quantity,
    SourceRef,
    Unit,
    UnitMismatchError,
)
from aer.core.enums import AnalysisMode, Grade, JobStatus, TransactionKind, UserRole
from aer.core.schemas.request import PortfolioContext, RequestPurpose
from aer.db.models import (
    Calculation,
    Company,
    Job,
    Portfolio,
    ReportSection,
    SectionDefinition,
    User,
)
from aer.db.models.report_section import SectionStatus
from aer.render.document import CalculationFootnote, assemble_document
from aer.render.markdown import serialise_markdown
from aer.sections import consequences as section
from aer.sections.deterministic import AUGMENTERS, CONSEQUENCES_KEY
from aer.sections.registry import resolve_sections
from aer.services.assumptions import confirm, propose
from aer.services.calculations import lineage
from aer.services.valuation_run import value_the_business
from aer.web.forms import FORM_FIELDS, form_values_from, parse_request_form
from tests import portfolio_fixtures
from tests.assumption_fixtures import analysed, seed_years
from tests.portfolio_fixtures import AS_OF, funded, trade
from tests.request_fixtures import research_request
from tests.test_composed_view import _CONFIRMED, _SHARES, _YEARS, MANDATE, _price

pytestmark = pytest.mark.integration

book = portfolio_fixtures.book

# -- Strategies ------------------------------------------------------------------------------

fractions = st.decimals(min_value=0, max_value=1, places=4, allow_nan=False, allow_infinity=False)
money = st.decimals(
    min_value=1, max_value=1_000_000, places=2, allow_nan=False, allow_infinity=False
)


def _weight(value: Decimal, label: str = "w") -> Quantity:
    return Quantity.of(value, DIMENSIONLESS, source=SourceRef.security("s", label=label))


def _usd(value: Decimal, label: str = "x") -> Quantity:
    return Quantity.of(
        value, Unit.currency("USD"), source=SourceRef.financial_fact("f", label=label)
    )


# -- The arithmetic ----------------------------------------------------------------------------


class TestTheArithmetic:
    @settings(max_examples=60)
    @given(cash=fractions, current=fractions, planned=fractions)
    def test_cash_after_is_cash_less_what_the_trade_takes(
        self, cash: Decimal, current: Decimal, planned: Decimal
    ) -> None:
        context = CalculationContext(code_version="test")

        after = calc.cash_weight_after(
            context,
            cash_weight=_weight(cash),
            current_weight=_weight(current),
            planned_weight=_weight(planned),
        )

        assert after.value == cash - (planned - current)
        assert after.unit == DIMENSIONLESS
        assert context.records[-1].name == "cash_weight_after"

    @settings(max_examples=60)
    @given(others=st.lists(fractions, max_size=8), planned=fractions)
    def test_the_top_five_after_ranks_the_plan_among_the_rest(
        self, others: list[Decimal], planned: Decimal
    ) -> None:
        context = CalculationContext(code_version="test")

        after = calc.top_holdings_share_after(
            context,
            other_weights=[_weight(value) for value in others],
            planned_weight=_weight(planned),
            count=5,
        )

        assert after.value == sum(sorted([*others, planned], reverse=True)[:5], Decimal(0))
        assert after.value >= min(planned, after.value)

    @settings(max_examples=60)
    @given(sector=fractions, current=fractions, planned=fractions)
    def test_the_sector_after_moves_by_the_difference(
        self, sector: Decimal, current: Decimal, planned: Decimal
    ) -> None:
        context = CalculationContext(code_version="test")

        after = calc.exposure_after(
            context,
            sector_share=_weight(sector),
            current_weight=_weight(current),
            planned_weight=_weight(planned),
            sector="Software",
        )

        assert after.value == sector + planned - current
        assert context.records[-1].parameters["sector"] == "Software"

    def test_a_weight_with_a_unit_is_refused(self) -> None:
        context = CalculationContext(code_version="test")
        with pytest.raises(UnitMismatchError):
            calc.cash_weight_after(
                context,
                cash_weight=_usd(Decimal(100)),
                current_weight=_weight(Decimal(0)),
                planned_weight=_weight(Decimal("0.05")),
            )

    @settings(max_examples=60)
    @given(flows=st.lists(money, min_size=1, max_size=8), value=money)
    def test_a_payback_exists_exactly_when_the_forecast_recovers_the_value(
        self, flows: list[Decimal], value: Decimal
    ) -> None:
        context = CalculationContext(code_version="test")
        present_values = [_usd(flow, f"pv{index}") for index, flow in enumerate(flows)]
        market = _usd(value, "ev")

        recovery = calc.forecast_recovery(
            context, present_values=present_values, market_enterprise_value=market
        )

        assert recovery.value == sum(flows, Decimal(0)) / value
        if recovery.value >= 1:
            year = calc.payback_year(
                context, present_values=present_values, market_enterprise_value=market
            )
            assert 1 <= year.value <= len(flows)
            assert sum(flows[: int(year.value)], Decimal(0)) >= value
            assert sum(flows[: int(year.value) - 1], Decimal(0)) < value
        else:
            with pytest.raises(CalculationError, match="beyond"):
                calc.payback_year(
                    context, present_values=present_values, market_enterprise_value=market
                )


# -- The scene ---------------------------------------------------------------------------------


async def _commissioned(
    session: AsyncSession,
    book: dict[str, Any],
    *,
    planned: str | None = "0.05",
    purpose: str | None = "add",
    ticker: str = "MSFT",
    exchange: str = "NASDAQ",
) -> dict[str, Any]:
    """A research request by the book's owner, dated the book's as-of date, with a run."""
    context: dict[str, Any] = {}
    if planned is not None:
        context["planned_weight"] = planned
    if purpose is not None:
        context["purpose"] = purpose
    request = research_request(
        user_id=book["user"].id,
        company_name=f"{ticker} Corporation",
        ticker=ticker,
        exchange=exchange,
        as_of_date=AS_OF,
        base_currency="USD",
        investment_horizon_months=36,
        horizon_label="Through the next cycle",
        max_cost_gbp="2.50",
        portfolio_context=context,
    )
    session.add(request)
    await session.flush()
    job = Job(
        work_order_id=request.id,
        workflow_version="test",
        code_version="abc",
        status=JobStatus.RUNNING,
        started_at=datetime.now(UTC),
    )
    session.add(job)
    await session.flush()
    return {"request": request, "job": job}


async def _held(
    session: AsyncSession, book: dict[str, Any], *, grade: Grade = Grade.ATTESTED
) -> None:
    await funded(session, book, grade=grade)
    await trade(session, book, security=book["msft"], quantity="10", price="400", grade=grade)


async def _classified(session: AsyncSession, book: dict[str, Any]) -> Company:
    company = Company(
        name="Microsoft Corporation",
        ticker="MSFT",
        exchange="NASDAQ",
        cik="0000789019",
        sic_description="Services-Prepackaged Software",
    )
    session.add(company)
    await session.flush()
    book["msft"].company_id = company.id
    await session.flush()
    return company


async def _rows(session: AsyncSession, job_id: Any) -> dict[str, int]:
    counted = await session.execute(
        select(Calculation.name, func.count())
        .where(Calculation.job_id == job_id)
        .group_by(Calculation.name)
    )
    return dict(counted.all())


def _by_label(block: dict[str, Any], field: str = "consequences") -> dict[str, dict[str, str]]:
    return {row["label"]: row for row in block[field]}


# -- The block ---------------------------------------------------------------------------------


class TestTheBlock:
    async def test_it_strikes_the_book_before_and_after_and_footnotes_every_figure(
        self, db_session: AsyncSession, book: dict[str, Any]
    ) -> None:
        await _held(db_session, book)
        scene = await _commissioned(db_session, book)

        block = await section.consequences_block(
            db_session, job_id=scene["job"].id, request=scene["request"]
        )

        rows = _by_label(block)
        assert "Planned share of the book in MSFT" in rows
        assert rows["Planned share of the book in MSFT"]["value"] == "0.05"
        assert "adding to a position" in rows["Planned share of the book in MSFT"]["provenance"]
        held = rows["Share of the book in MSFT today"]
        cash_today = rows["Share of the book in cash today"]
        cash_after = rows["Share of the book in cash after"]
        top_today = rows["Share of the book in its five largest holdings today"]
        top_after = rows["Share of the book in its five largest holdings after"]
        # The book holds one listing, so its top five today is that holding; after the
        # plan it is the planned weight, and the cash falls by the difference.
        assert Decimal(held["value"]) > 0
        assert Decimal(top_today["value"]) == Decimal(held["value"])
        assert Decimal(top_after["value"]) == Decimal("0.05")
        # The stored row is struck at full precision and kept at twelve places; the inputs
        # beside it are shown at the same, so the identity holds to the last stored place.
        expected = Decimal(cash_today["value"]) - (Decimal("0.05") - Decimal(held["value"]))
        assert abs(Decimal(cash_after["value"]) - expected) <= Decimal("1e-11")
        # Every computed figure footnotes a stored calculation of its own.
        for label in (
            "Share of the book in MSFT today",
            "Share of the book in cash today",
            "Share of the book in cash after",
            "Share of the book in its five largest holdings today",
            "Share of the book in its five largest holdings after",
        ):
            assert rows[label]["calculation_id"], label
            assert await db_session.get(Calculation, rows[label]["calculation_id"]) is not None
        assert "funded from cash" in block["basis"]
        assert "nothing here is a recommendation" in block["basis"]

    async def test_a_listing_not_held_starts_from_nothing(
        self, db_session: AsyncSession, book: dict[str, Any]
    ) -> None:
        await funded(db_session, book)
        await trade(
            db_session, book, security=book["barc"], quantity="100", price="250", currency="GBX"
        )
        scene = await _commissioned(db_session, book, purpose="new_position")

        block = await section.consequences_block(
            db_session, job_id=scene["job"].id, request=scene["request"]
        )

        rows = _by_label(block)
        today = rows["Share of the book in MSFT today"]
        assert Decimal(today["value"]) == 0
        assert "holds none of MSFT" in today["provenance"]
        # A nil the book walk established carries no marker of its own: the marker would
        # land on the whole book's total, which is true and unhelpful.
        assert "calculation_id" not in today
        # Barclays is the only holding today; after the plan the five largest are it and
        # the planned position together.
        top_today = Decimal(rows["Share of the book in its five largest holdings today"]["value"])
        top_after = Decimal(rows["Share of the book in its five largest holdings after"]["value"])
        assert top_after == top_today + Decimal("0.05")

    async def test_a_classified_listing_moves_its_sector(
        self, db_session: AsyncSession, book: dict[str, Any]
    ) -> None:
        await _held(db_session, book)
        await _classified(db_session, book)
        scene = await _commissioned(db_session, book)

        block = await section.consequences_block(
            db_session, job_id=scene["job"].id, request=scene["request"]
        )

        rows = _by_label(block)
        today = rows["Share of the book in Services-Prepackaged Software today"]
        after = rows["Share of the book in Services-Prepackaged Software after"]
        assert Decimal(today["value"]) == Decimal(rows["Share of the book in MSFT today"]["value"])
        assert Decimal(after["value"]) == Decimal("0.05")
        assert after["calculation_id"]

    async def test_it_strikes_once_and_composes_from_the_ledger_after_that(
        self, db_session: AsyncSession, book: dict[str, Any]
    ) -> None:
        await _held(db_session, book)
        scene = await _commissioned(db_session, book)

        first = await section.consequences_block(
            db_session, job_id=scene["job"].id, request=scene["request"]
        )
        before = await _rows(db_session, scene["job"].id)
        second = await section.consequences_block(
            db_session, job_id=scene["job"].id, request=scene["request"]
        )
        after = await _rows(db_session, scene["job"].id)

        assert before == after
        assert before["cash_weight_after"] == 1
        assert before["top_holdings_share_after"] == 1
        assert first["consequences"] == second["consequences"]
        assert first["grade"] == second["grade"] == Grade.ATTESTED.value

    async def test_the_grade_is_the_books_own(
        self, db_session: AsyncSession, book: dict[str, Any]
    ) -> None:
        """A typed book gives an attested section; a documented one crosses unchanged."""
        await _held(db_session, book, grade=Grade.DOCUMENTED)
        scene = await _commissioned(db_session, book)

        block = await section.consequences_block(
            db_session, job_id=scene["job"].id, request=scene["request"]
        )

        assert block["grade"] == Grade.DOCUMENTED.value
        assert block["attested_inputs"] == []
        assert "crosses to a shared copy unchanged" in block["evidence_note"]

    async def test_a_typed_book_names_what_was_typed(
        self, db_session: AsyncSession, book: dict[str, Any]
    ) -> None:
        await _held(db_session, book)
        scene = await _commissioned(db_session, book)

        block = await section.consequences_block(
            db_session, job_id=scene["job"].id, request=scene["request"]
        )

        assert block["grade"] == Grade.ATTESTED.value
        assert block["attested_inputs"]
        assert (
            "withheld from any copy of the report that leaves this machine"
            in (block["evidence_note"])
        )
        # The stored lineage says the same: the walk from the cash figure — the one that
        # reaches the typed trades — meets an attested leaf, which is what a later
        # composition reads the grade from.
        cash_after = _by_label(block)["Share of the book in cash after"]
        tree = await lineage(db_session, cash_after["calculation_id"])
        assert any(
            node.kind == "attestation" and node.detail.get("grade") == Grade.ATTESTED.value
            for node in tree.walk()
        )

    async def test_the_planned_weight_is_an_input_the_lineage_names(
        self, db_session: AsyncSession, book: dict[str, Any]
    ) -> None:
        await _held(db_session, book)
        scene = await _commissioned(db_session, book)
        block = await section.consequences_block(
            db_session, job_id=scene["job"].id, request=scene["request"]
        )

        row = _by_label(block)["Share of the book in cash after"]
        tree = await lineage(db_session, row["calculation_id"])

        planned = [node for node in tree.walk() if node.detail.get("table") == "research_requests"]
        assert planned, "the planned weight resolved to no request"
        assert planned[0].kind == "assumption"
        assert planned[0].value == Decimal("0.05")
        assert planned[0].detail["purpose"] == "add"

    async def test_no_book_on_record_is_a_sentence_and_no_figure(
        self, db_session: AsyncSession
    ) -> None:
        user = User(email="nobook@example.invalid", display_name="N", role=UserRole.OWNER)
        db_session.add(user)
        await db_session.flush()
        scene = await _commissioned(db_session, {"user": user})

        block = await section.consequences_block(
            db_session, job_id=scene["job"].id, request=scene["request"]
        )

        assert "no book is on record" in block["basis"]
        assert "consequences" not in block
        assert section.consequences_only(block) == block["basis"]
        assert await _rows(db_session, scene["job"].id) == {}

    async def test_no_planned_weight_is_no_block_at_all(
        self, db_session: AsyncSession, book: dict[str, Any]
    ) -> None:
        await _held(db_session, book)
        scene = await _commissioned(db_session, book, planned=None, purpose=None)

        block = await section.consequences_block(
            db_session, job_id=scene["job"].id, request=scene["request"]
        )

        assert block == {}
        assert await _rows(db_session, scene["job"].id) == {}

    async def test_the_horizon_says_when_no_payback_could_be_measured(
        self, db_session: AsyncSession, book: dict[str, Any]
    ) -> None:
        await _held(db_session, book)
        scene = await _commissioned(db_session, book)

        block = await section.consequences_block(
            db_session, job_id=scene["job"].id, request=scene["request"]
        )

        horizon = _by_label(block, "horizon")
        assert horizon["Your stated horizon"]["value"] == "36"
        assert "Through the next cycle" in horizon["Your stated horizon"]["provenance"]
        assert "Not measured" in horizon["The model's payback"]["provenance"]


# -- The section applies only to a planned weight -----------------------------------------------


class TestApplicability:
    async def test_the_section_is_absent_without_a_planned_weight(
        self, db_session: AsyncSession, book: dict[str, Any]
    ) -> None:
        without = await _commissioned(db_session, book, planned=None, purpose=None)
        with_weight = await _commissioned(db_session, book)

        absent = [row.key for row in await resolve_sections(db_session, request=without["request"])]
        present = [
            row.key for row in await resolve_sections(db_session, request=with_weight["request"])
        ]

        assert CONSEQUENCES_KEY not in absent
        assert present[-1] == CONSEQUENCES_KEY

    def test_the_registry_binds_the_key(self) -> None:
        augmenter = AUGMENTERS[CONSEQUENCES_KEY]
        assert augmenter.build is section.consequences_block
        assert augmenter.check is section.consequences_problems
        assert augmenter.standalone is section.consequences_only
        assert augmenter.note is section.consequences_note


# -- The audience --------------------------------------------------------------------------------


def _content(grade: Grade) -> dict[str, Any]:
    return {
        "basis": "Computed from your book.",
        "consequences": [
            {
                "label": "Share of the book in cash after",
                "value": "0.4",
                "unit": "pure",
                "provenance": "Computed",
                "calculation_id": "11111111-1111-1111-1111-111111111111",
            }
        ],
        "horizon": [],
        "commentary": "The trade would take the cash weight down to forty per cent.",
        "evidence_note": "Where these come from.",
        "grade": grade.value,
        "attested_inputs": ["MSFT bought 15 June 2026"] if grade is Grade.ATTESTED else [],
    }


class TestTheAudience:
    def test_the_operators_copy_keeps_everything(self) -> None:
        content = _content(Grade.ATTESTED)
        assert section.consequences_for_audience(content, Audience.INTERNAL) is content

    def test_a_documented_lineage_crosses_unchanged(self) -> None:
        content = _content(Grade.DOCUMENTED)
        assert section.consequences_for_audience(content, Audience.SHAREABLE) is content

    def test_an_attested_lineage_leaves_as_a_disclosure_and_nothing_else(self) -> None:
        """Not the rows and not the commentary: prose that quotes a withheld figure is the
        figure in another notation."""
        shared = section.consequences_for_audience(_content(Grade.ATTESTED), Audience.SHAREABLE)

        assert set(shared) == {"withheld"}
        assert "MSFT bought 15 June 2026" in shared["withheld"]
        assert "withheld from this version" in shared["withheld"]
        assert "0.4" not in shared["withheld"]
        assert "forty per cent" not in shared["withheld"]

    async def test_the_document_carries_the_section_to_each_audience(
        self, db_session: AsyncSession, book: dict[str, Any]
    ) -> None:
        await _held(db_session, book)
        scene = await _commissioned(db_session, book)
        block = await section.consequences_block(
            db_session, job_id=scene["job"].id, request=scene["request"]
        )
        definition = await db_session.scalar(
            select(SectionDefinition).where(SectionDefinition.key == CONSEQUENCES_KEY)
        )
        assert definition is not None
        db_session.add(
            ReportSection(
                job_id=scene["job"].id,
                section_definition_id=definition.id,
                section_key=CONSEQUENCES_KEY,
                position=definition.position,
                status=SectionStatus.GENERATED,
                content={
                    **block,
                    "commentary": "At the planned share the cash falls and the five largest grow.",
                },
            )
        )
        await db_session.flush()

        own = await assemble_document(
            db_session, job=scene["job"], request=scene["request"], audience=Audience.INTERNAL
        )
        shared = await assemble_document(db_session, job=scene["job"], request=scene["request"])

        own_text = serialise_markdown(own)
        shared_text = serialise_markdown(shared)
        assert "Your Book, Before And After" in own_text
        assert "cash falls" in own_text
        assert all(isinstance(note, CalculationFootnote) for note in own.footnotes)
        assert own.footnotes, "the operator's copy footnotes every figure"
        assert "Withheld From This Copy" in shared_text
        assert "Your Book, Before And After" not in shared_text
        assert "cash falls" not in shared_text
        assert not shared.footnotes


# -- The commentary's edge -------------------------------------------------------------------------


class TestTheCommentary:
    def test_a_consequence_is_allowed_and_an_instruction_is_refused(self) -> None:
        block = _content(Grade.DOCUMENTED)

        allowed = section.consequences_problems(
            {"commentary": "The trade would increase the five largest to half the book."}, block
        )
        refused = section.consequences_problems(
            {"commentary": "We recommend adding at this price; you should size to five per cent."},
            block,
        )

        assert allowed == []
        assert len(refused) == 2
        assert "'you should'" in refused[0]
        assert "'recommend'" in refused[1]
        assert "Share of the book in cash after" in refused[0]

    def test_the_note_names_the_figures_and_forbids_the_instruction(self) -> None:
        note = section.consequences_note(_content(Grade.DOCUMENTED))
        assert "Share of the book in cash after" in note
        assert "no buy, sell, add, trim" in note

    def test_no_figures_means_no_writer_call(self) -> None:
        assert section.consequences_only({"basis": "No book."}) == "No book."
        assert section.consequences_only(_content(Grade.DOCUMENTED)) == ""


# -- The request form ------------------------------------------------------------------------------


class TestTheForm:
    def test_the_context_fields_round_trip(self) -> None:
        request = research_request(
            user_id=None,
            company_name="Contoso",
            ticker="CTSO",
            exchange="LSE",
            as_of_date=AS_OF,
            base_currency="GBP",
            investment_horizon_months=24,
            analysis_mode=AnalysisMode.STANDARD,
            max_cost_gbp="5.00",
            undated_sources_admissible=True,
            portfolio_context={"planned_weight": "0.05", "purpose": "review"},
        )

        values = form_values_from(request)

        assert values["planned_weight_percent"] == "5"
        assert values["purpose"] == "review"
        assert set(values) == set(FORM_FIELDS)
        parsed = parse_request_form(values)
        assert parsed.payload is not None
        assert parsed.payload.portfolio_context.planned_weight == Decimal("0.05")
        assert parsed.payload.portfolio_context.purpose is RequestPurpose.REVIEW

    def test_a_blank_context_is_no_closing_section(self) -> None:
        context = PortfolioContext(planned_weight=None, purpose="")
        assert context.is_empty()
        assert context.purpose is None

    def test_a_weight_past_the_book_and_a_purpose_nobody_offered_are_refused(self) -> None:
        values = dict.fromkeys(FORM_FIELDS, "")
        values.update(
            {
                "company_name": "Contoso",
                "ticker": "CTSO",
                "exchange": "LSE",
                "base_currency": "GBP",
                "investment_horizon_months": "12",
                "analysis_mode": "standard",
                "max_cost_gbp": "5.00",
                "planned_weight_percent": "150",
                "purpose": "gamble",
            }
        )

        parsed = parse_request_form(values)

        assert parsed.payload is None
        assert "planned_weight_percent" in parsed.errors
        assert "purpose" in parsed.errors


# -- The horizon against the model's payback -----------------------------------------------------


async def _valued_at_market(scene: dict[str, Any], *, price: str) -> None:
    """A run that reached a valuation and holds a market price and capitalisation.

    The composed view's own scene, with the capitalisation the horizon figures need: a
    payback against a price the run does not have is not a figure, so
    :func:`value_the_business` strikes the recovery only where the run holds one.
    """
    await seed_years(scene, _YEARS)
    actor = User(email="operator@example.invalid", display_name="O", role=UserRole.OWNER)
    scene["session"].add(actor)
    await scene["session"].flush()
    for name, value in _CONFIRMED.items():
        assumption = await propose(
            scene["session"],
            request_id=scene["request"].id,
            name=name,
            value=Decimal(value),
            unit="pure",
            justification=f"Scene value for {name}.",
            proposed_by="test",
        )
        await confirm(scene["session"], assumption=assumption, actor=actor)
    capitalisation = Quantity.of(
        Decimal(price) * Decimal(_SHARES["diluted_shares_outstanding"]),
        Unit.currency("USD"),
        source=SourceRef.security("a-listing", label="market capitalisation"),
    )
    await value_the_business(
        scene["session"],
        request=scene["request"],
        job_id=scene["job"].id,
        analysis=await analysed(scene),
        mandate=MANDATE,
        years=5,
        market_capitalisation=capitalisation,
        price_per_share=_price(price),
    )


class TestTheHorizon:
    async def test_the_value_step_strikes_the_recovery_and_a_payback_only_where_reached(
        self, scene: dict[str, Any]
    ) -> None:
        await _valued_at_market(scene, price="50")

        names = await _rows(scene["session"], scene["job"].id)
        recovery = await scene["session"].scalar(
            select(Calculation).where(
                Calculation.job_id == scene["job"].id, Calculation.name == "forecast_recovery"
            )
        )

        assert names["market_enterprise_value"] == 1
        assert names["forecast_recovery"] == 1
        assert recovery is not None
        assert ("payback_year" in names) == (recovery.output_value >= 1)

    async def test_a_valuation_without_a_price_strikes_no_horizon_figure(
        self, scene: dict[str, Any]
    ) -> None:
        """A payback against a price the run does not hold is not a figure."""
        await seed_years(scene, _YEARS)
        actor = User(email="operator@example.invalid", display_name="O", role=UserRole.OWNER)
        scene["session"].add(actor)
        await scene["session"].flush()
        for name, value in _CONFIRMED.items():
            assumption = await propose(
                scene["session"],
                request_id=scene["request"].id,
                name=name,
                value=Decimal(value),
                unit="pure",
                justification=f"Scene value for {name}.",
                proposed_by="test",
            )
            await confirm(scene["session"], assumption=assumption, actor=actor)

        await value_the_business(
            scene["session"],
            request=scene["request"],
            job_id=scene["job"].id,
            analysis=await analysed(scene),
            mandate=MANDATE,
            years=5,
        )

        names = await _rows(scene["session"], scene["job"].id)
        assert "forecast_recovery" not in names
        assert "payback_year" not in names

    async def test_the_section_sets_the_stated_horizon_against_it(
        self, scene: dict[str, Any]
    ) -> None:
        await _valued_at_market(scene, price="50")
        session = scene["session"]
        # A book for the scene's operator: sterling cash and nothing held, so the figures
        # are the plan alone and the horizon rows are what this test is about.
        portfolio = Portfolio(
            user_id=scene["request"].work_order.user_id, name="Book", base_currency="GBP"
        )
        session.add(portfolio)
        await session.flush()
        # Funded before the scene's own as-of date, which is in 2022: a deposit the book
        # walk cannot see is a book that would not value.
        await trade(
            session,
            {"portfolio": portfolio, "document": None},
            kind=TransactionKind.DEPOSIT,
            security=None,
            price=None,
            quantity="100000",
            currency="GBP",
            on=date(2022, 6, 1),
        )
        scene["request"].portfolio_context = {"planned_weight": "0.05", "purpose": "new_position"}
        await session.flush()

        block = await section.consequences_block(
            session, job_id=scene["job"].id, request=scene["request"]
        )

        horizon = _by_label(block, "horizon")
        assert horizon["Your stated horizon"]["value"] == "12"
        recovery = horizon["Share of today's enterprise value the explicit forecast recovers"]
        assert recovery["calculation_id"]
        assert "5-year explicit forecast" in recovery["provenance"]
        if "The model's payback year" in horizon:
            assert horizon["The model's payback year"]["calculation_id"]
        else:
            assert (
                "Beyond the 5-year explicit forecast"
                in horizon["The model's payback"]["provenance"]
            )
        rows = _by_label(block)
        assert Decimal(rows["Share of the book in its five largest holdings after"]["value"]) == (
            Decimal("0.05")
        )
        assert "Share of the book in its five largest holdings today" not in rows
