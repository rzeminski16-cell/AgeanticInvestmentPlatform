"""Provenance: what is recorded, what is refused, and how lineage resolves.

The acceptance criterion for the whole task lives here: **no numeric result can exist
without a persisted formula, inputs and code version**, and a traced call with an unsourced
numeric input raises.

The database half computes a revenue CAGR from real financial facts and checks that the
resulting row's inputs resolve back to those exact fact ids — the end-to-end claim, made
against a real Postgres rather than argued from the code.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from aer.calc.basic import cagr, growth_rate, margin, ratio, weighted_average
from aer.calc.engine import CalculationContext, CalculationRecord, traced
from aer.calc.units import (
    CalculationError,
    Quantity,
    SourceKind,
    SourceRef,
    SourceTable,
    UnsourcedValueError,
    money,
)
from aer.calc.units import ratio as pure
from aer.core.enums import FactBasis, JobStatus, Provider, RequestStatus, SourceTier, UserRole
from aer.db.models import (
    Assumption,
    Calculation,
    FinancialFact,
    Job,
    MacroObservationRow,
    MacroSeriesRow,
    ResearchRequest,
    Security,
    User,
)
from aer.errors import ValidationError
from aer.services import calculations as calculation_service
from aer.services.acquisition import record_acquisition
from aer.services.facts import upsert_company
from aer.sources.base import ResolvedEntity
from aer.storage.local import LocalArtefactStore
from tests.sec_fixtures import MSFT_CIK, fixture_bytes
from tests.test_sec_persistence import fetched

SOURCE = SourceRef.financial_fact("fact-1")


@pytest.fixture
def context():
    return CalculationContext(code_version="a1b2c3d4")


def usd(value, source=SOURCE):
    return money(value, "USD", source=source)


async def count_for(session, job) -> int:
    """How many calculations this job produced.

    Scoped to the job rather than counting the whole table. Other test modules commit
    rows for real -- the API tests need committed data to be visible to the application's
    own session -- so a global count would make this test's result depend on which tests
    ran before it.
    """
    return await session.scalar(
        select(func.count()).select_from(Calculation).where(Calculation.job_id == job.id)
    )


# ==========================================================================================
# The refusal — the rule the whole task turns on
# ==========================================================================================


class TestUnsourcedInputsAreRefused:
    def test_a_quantity_with_no_source_raises(self, context):
        # A number that cannot say where it came from produces a figure nobody can defend,
        # and a report is only as good as its worst-sourced number.
        with pytest.raises(UnsourcedValueError, match="has no source"):
            growth_rate(context, start=money(100, "USD"), end=usd(110))

    def test_a_bare_decimal_raises(self, context):
        # Explicitly required by the specification. Accepting it would be convenient, and
        # convenience here means an unaccountable figure in a report.
        with pytest.raises(UnsourcedValueError, match="bare Decimal"):
            growth_rate(context, start=Decimal(100), end=usd(110))  # type: ignore[arg-type]

    def test_a_bare_float_raises(self, context):
        with pytest.raises(UnsourcedValueError, match="bare float"):
            growth_rate(context, start=100.0, end=usd(110))  # type: ignore[arg-type]

    def test_the_refusal_names_the_offending_input(self, context):
        with pytest.raises(UnsourcedValueError) as excinfo:
            growth_rate(context, start=usd(100), end=money(110, "USD"))

        assert excinfo.value.context["input"] == "end"
        assert excinfo.value.context["calculation"] == "growth_rate"

    def test_a_refused_call_records_nothing(self, context):
        # A failed calculation must leave no trace, or the ledger accumulates records for
        # numbers that were never produced.
        with pytest.raises(UnsourcedValueError):
            growth_rate(context, start=money(100, "USD"), end=usd(110))

        assert len(context) == 0

    def test_an_unsourced_element_of_a_series_raises(self, context):
        # Each element is checked, not just the sequence. One unsourced peer in a
        # comparable-company average is one unaccountable contribution to the answer.
        with pytest.raises(UnsourcedValueError, match=r"values\[1\]"):
            weighted_average(
                context,
                values=[usd(10), money(20, "USD")],
                weights=[pure(1, source=SOURCE), pure(3, source=SOURCE)],
            )

    def test_a_calculation_failing_for_any_other_reason_also_records_nothing(self, context):
        with pytest.raises(CalculationError):
            growth_rate(context, start=usd(0), end=usd(100))

        assert len(context) == 0


# ==========================================================================================
# What gets recorded
# ==========================================================================================


class TestTheRecord:
    def test_every_field_the_specification_requires_is_present(self, context):
        cagr(context, start=usd(100), end=usd(200), years=3)

        record = context.records[0]
        assert record.name == "cagr"
        assert record.formula == "cagr = (end / start) ^ (1 / years) - 1"
        assert record.function_ref == "aer.calc.basic:cagr"
        assert record.code_version == "a1b2c3d4"
        assert record.output_unit == "pure"
        assert record.output_value > 0

    def test_inputs_carry_a_name_a_unit_and_a_source(self, context):
        growth_rate(context, start=usd(100), end=usd(110))

        inputs = {i.name: i for i in context.records[0].inputs}
        assert set(inputs) == {"start", "end"}
        assert inputs["start"].unit == "USD"
        assert inputs["start"].source_kind is SourceKind.FACT
        assert inputs["start"].source_id == "fact-1"

    def test_structural_parameters_are_recorded_separately_from_inputs(self, context):
        # `years` is a structural choice, not a measurement. Recording it as an input
        # would demand a fake source; recording it as a parameter keeps it auditable
        # without pretending it is evidence.
        cagr(context, start=usd(100), end=usd(200), years=3)

        record = context.records[0]
        assert record.parameters == {"years": 3}
        assert [i.name for i in record.inputs] == ["start", "end"]

    def test_declared_assumptions_are_recorded_on_every_invocation(self, context):
        cagr(context, start=usd(100), end=usd(200), years=3)

        assert any("equal length" in note for note in context.records[0].assumptions)

    def test_a_series_input_records_each_element_with_its_index(self, context):
        # A peer average must be traceable to which company contributed what.
        weighted_average(
            context,
            values=[usd(10, SourceRef.financial_fact("a")), usd(20, SourceRef.financial_fact("b"))],
            weights=[pure(1, source=SOURCE), pure(3, source=SOURCE)],
        )

        names = [i.name for i in context.records[0].inputs]
        assert names[:2] == ["values[0]", "values[1]"]

    def test_the_declared_formula_is_reachable_without_calling_anything(self):
        # So a provenance viewer can show the formula for a calculation nobody has run.
        assert cagr.formula == "cagr = (end / start) ^ (1 / years) - 1"
        assert cagr.calculation_name == "cagr"
        assert cagr.function_ref == "aer.calc.basic:cagr"

    def test_the_record_serialises_to_json_safe_values(self, context):
        cagr(context, start=usd(100), end=usd(200), years=3)

        payload = context.records[0].as_dict()

        # Values as strings, so a JSON round trip cannot turn an exact Decimal into a
        # float -- the one way a provenance record could disagree with what was computed.
        assert isinstance(payload["output_value"], str)
        assert isinstance(payload["inputs"][0]["value"], str)


class TestThePeriodStamp:
    """The reporting period travels on the record, set as a scope on the ledger.

    The live report printed an annual EBITDA beside a quarterly revenue and called the
    pair a margin; the stamp is what makes that mixture visible instead of silent.
    """

    def test_a_scope_stamps_every_record_struck_inside_it(self, context):
        with context.period("FY2025", start=date(2024, 9, 29), end=date(2025, 9, 27)):
            cagr(context, start=usd(100), end=usd(200), years=3)

        stamp = context.records[0].period
        assert stamp is not None
        assert stamp.label == "FY2025"
        assert stamp.start == date(2024, 9, 29)
        assert stamp.end == date(2025, 9, 27)

    def test_no_scope_means_no_stamp(self, context):
        # A discount rate or a multiple as at a date is not a statement-period figure,
        # and pretending otherwise would be a false provenance claim.
        cagr(context, start=usd(100), end=usd(200), years=3)

        assert context.records[0].period is None

    def test_leaving_the_scope_restores_what_it_replaced(self, context):
        with context.period("FY2024"):
            with context.period("FY2025"):
                cagr(context, start=usd(100), end=usd(200), years=3)
            growth_rate(context, start=usd(100), end=usd(110))
        cagr(context, start=usd(100), end=usd(300), years=2)

        labels = [r.period.label if r.period else None for r in context.records]
        assert labels == ["FY2025", "FY2024", None]

    def test_a_raise_unwinds_to_the_enclosing_scope_not_to_nothing(self, context):
        with context.period("FY2024"):
            with pytest.raises(UnsourcedValueError), context.period("FY2025"):
                cagr(context, start=Decimal(100), end=usd(200), years=3)

            assert context.current_period is not None
            assert context.current_period.label == "FY2024"

    def test_the_stamp_serialises_with_the_record(self, context):
        with context.period("FY2025", end=date(2025, 9, 27)):
            cagr(context, start=usd(100), end=usd(200), years=3)

        payload = context.records[0].as_dict()
        assert payload["period"] == {"label": "FY2025", "start": None, "end": "2025-09-27"}


class TestChaining:
    def test_a_result_is_attributed_to_its_own_calculation(self, context):
        result = growth_rate(context, start=usd(100), end=usd(110))

        assert result.source is not None
        assert result.source.kind is SourceKind.CALCULATION
        assert result.source.identifier == str(context.records[0].id)

    def test_a_result_can_be_fed_into_another_calculation(self, context):
        # What makes lineage a tree: a computed value is exactly as sourced as a fact.
        first = growth_rate(context, start=usd(100), end=usd(110))
        second = growth_rate(context, start=usd(100), end=usd(120))

        ratio(context, numerator=first, denominator=second)

        assert len(context) == 3
        final = context.records[2]
        assert all(i.source_kind is SourceKind.CALCULATION for i in final.inputs)

    def test_the_chain_records_which_calculation_fed_which(self, context):
        first = growth_rate(context, start=usd(100), end=usd(110))
        ratio(context, numerator=first, denominator=pure(2, source=SOURCE))

        assert context.records[1].inputs[0].source_id == str(context.records[0].id)

    def test_records_are_in_the_order_they_happened(self, context):
        # Load-bearing for persistence: a row citing another is never written first.
        growth_rate(context, start=usd(100), end=usd(110))
        margin(context, part=usd(30), whole=usd(100))

        assert [r.name for r in context.records] == ["growth_rate", "margin"]

    def test_a_context_can_be_searched(self, context):
        growth_rate(context, start=usd(100), end=usd(110))

        record = context.records[0]
        assert context.find(record.id) is record
        assert context.find(uuid.uuid4()) is None
        assert context.named("growth_rate") == (record,)


class TestTheDecorator:
    def test_a_traced_function_keeps_its_identity(self):
        assert cagr.__name__ == "cagr"
        assert cagr.__doc__ is not None
        assert "Compound annual growth rate" in cagr.__doc__

    def test_a_custom_traced_function_records_the_same_way(self, context):
        @traced(name="double", formula="double = x * 2")
        def double(_context: CalculationContext, *, x: Quantity) -> Quantity:
            return x * Quantity.of(2)

        result = double(context, x=usd(21))

        assert result.value == Decimal(42)
        assert context.records[0].formula == "double = x * 2"
        assert context.records[0].name == "double"

    def test_a_context_defaults_to_an_unknown_code_version(self):
        # Visible in the record rather than absent, so anyone trying to reproduce a figure
        # can see that the version was not captured.
        assert CalculationContext().code_version == "unknown"


# ==========================================================================================
# Persistence and lineage, against a real database
# ==========================================================================================


@pytest.fixture
def store(tmp_path) -> LocalArtefactStore:
    return LocalArtefactStore(tmp_path / "artefacts", max_bytes=1_000_000)


@pytest.fixture
async def request_row(db_session) -> ResearchRequest:
    user = User(email="calc@example.invalid", display_name="Calc", role=UserRole.OWNER)
    db_session.add(user)
    await db_session.flush()

    row = ResearchRequest(
        user_id=user.id,
        company_name="Microsoft Corporation",
        ticker="MSFT",
        exchange="NASDAQ",
        as_of_date=date(2023, 1, 1),
        base_currency="USD",
        investment_horizon_months=36,
        max_cost_gbp="2.00",
        portfolio_context={},
        point_in_time=True,
        status=RequestStatus.DRAFT,
    )
    db_session.add(row)
    await db_session.flush()
    return row


@pytest.fixture
async def job(db_session, request_row) -> Job:
    row = Job(
        request_id=request_row.id,
        workflow_version="test-1",
        code_version="a1b2c3d4",
        status=JobStatus.RUNNING,
    )
    db_session.add(row)
    await db_session.flush()
    return row


@pytest.fixture
async def revenue_facts(db_session, store, request_row) -> list[FinancialFact]:
    """Three years of revenue, each traced to a real source document and artefact."""
    result = await fetched(store, fixture_bytes("companyfacts_msft.json"))
    acquisition = await record_acquisition(
        db_session,
        store,
        request=request_row,
        result=result,
        provider=Provider.SEC_EDGAR,
        source_tier=SourceTier.T1_REGULATORY,
    )
    company = await upsert_company(
        db_session,
        entity=ResolvedEntity(identifier=MSFT_CIK, name="MICROSOFT CORPORATION"),
        ticker="MSFT",
        exchange="NASDAQ",
    )

    rows = [
        FinancialFact(
            company_id=company.id,
            source_document_id=acquisition.source_document.id,
            concept="revenue",
            raw_concept="Revenues",
            taxonomy="us-gaap",
            value=Decimal(value),
            unit="USD",
            period_end=date(year, 6, 30),
            fiscal_year=year,
            fiscal_period="FY",
            filed_date=date(year, 7, 30),
            form="10-K",
            accession=accession,
            basis=FactBasis.AS_REPORTED,
        )
        for year, value, accession in (
            (2020, "143015000000", "0000789019-20-000039"),
            (2021, "168088000000", "0000789019-21-000027"),
            (2022, "198270000000", "0000789019-22-000010"),
        )
    ]
    db_session.add_all(rows)
    await db_session.flush()
    return rows


def fact_quantity(fact: FinancialFact, label: str = "") -> Quantity:
    return money(fact.value, "USD", source=SourceRef.financial_fact(fact.id, label=label))


@pytest.mark.integration
class TestPersistence:
    async def test_a_context_persists_every_record(self, db_session, job, context):
        growth_rate(context, start=usd(100), end=usd(110))
        margin(context, part=usd(30), whole=usd(100))

        rows = await calculation_service.persist_context(db_session, context, job_id=job.id)

        assert len(rows) == 2
        assert await count_for(db_session, job) == 2

    async def test_the_persisted_row_carries_formula_inputs_and_code_version(
        self, db_session, job, context
    ):
        # The acceptance criterion: no numeric result exists without these three.
        cagr(context, start=usd(100), end=usd(200), years=3)

        await calculation_service.persist_context(db_session, context, job_id=job.id)
        row = await db_session.scalar(select(Calculation).where(Calculation.job_id == job.id))

        assert row is not None
        assert row.formula == "cagr = (end / start) ^ (1 / years) - 1"
        assert row.code_version == "a1b2c3d4"
        assert row.function_ref == "aer.calc.basic:cagr"
        assert len(row.inputs) == 2
        assert row.parameters == {"years": 3}

    async def test_the_exact_decimal_survives_the_round_trip(self, db_session, job, context):
        margin(context, part=usd("44281000000"), whole=usd("143015000000"))

        await calculation_service.persist_context(db_session, context, job_id=job.id)
        db_session.expunge_all()
        row = await db_session.scalar(select(Calculation).where(Calculation.job_id == job.id))

        assert row is not None
        assert row.output_value == Decimal("0.309624864525")

    async def test_the_period_stamp_reaches_the_row(self, db_session, job, context):
        with context.period("FY2025", start=date(2024, 9, 29), end=date(2025, 9, 27)):
            margin(context, part=usd(30), whole=usd(100))
        # Struck outside any scope: not a statement-period figure, and honestly so.
        growth_rate(context, start=usd(100), end=usd(110))

        await calculation_service.persist_context(db_session, context, job_id=job.id)
        rows = list(
            await db_session.scalars(
                select(Calculation)
                .where(Calculation.job_id == job.id)
                .order_by(Calculation.sequence)
            )
        )

        assert rows[0].period_label == "FY2025"
        assert rows[0].period_start == date(2024, 9, 29)
        assert rows[0].period_end == date(2025, 9, 27)
        assert rows[1].period_label is None
        assert rows[1].period_end is None

    async def test_persisting_an_empty_context_raises(self, db_session, job):
        # Almost always a caller that passed its traced functions a different context from
        # the one being saved, which would otherwise show up as a report with no numbers.
        with pytest.raises(ValidationError, match="empty"):
            await calculation_service.persist_context(
                db_session, CalculationContext(), job_id=job.id
            )

    async def test_a_failed_persist_writes_nothing(self, db_session, job, context):
        # All-or-nothing. Half a provenance chain has rows whose inputs reference
        # calculations that do not exist -- worse than no record, because it looks
        # resolvable until somebody tries.
        growth_rate(context, start=usd(100), end=usd(110))
        growth_rate(context, start=usd(100), end=usd(120))

        poisoned = CalculationContext(code_version="a1b2c3d4")
        for index, record in enumerate(context.records):
            # A blank formula violates the check constraint, on the second row only.
            poisoned.add(
                CalculationRecord(
                    id=record.id,
                    name=record.name,
                    formula="   " if index == 1 else record.formula,
                    function_ref=record.function_ref,
                    code_version=record.code_version,
                    inputs=record.inputs,
                    output_value=record.output_value,
                    output_unit=record.output_unit,
                )
            )

        with pytest.raises(Exception, match="formula_is_not_blank"):
            await calculation_service.persist_context(db_session, poisoned, job_id=job.id)

        assert await count_for(db_session, job) == 0


@pytest.mark.integration
class TestLineage:
    async def test_a_cagr_over_facts_resolves_to_those_exact_fact_ids(
        self, db_session, job, revenue_facts, context
    ):
        # The end-to-end claim of the task, against a real database.
        first, _, last = revenue_facts

        cagr(
            context,
            start=fact_quantity(first, "revenue FY2020"),
            end=fact_quantity(last, "revenue FY2022"),
            years=2,
        )
        rows = await calculation_service.persist_context(db_session, context, job_id=job.id)

        tree = await calculation_service.lineage(db_session, rows[0].id)

        assert {node.identifier for node in tree.leaves} == {str(first.id), str(last.id)}
        assert all(node.kind == "fact" for node in tree.leaves)

    async def test_a_leaf_carries_the_filing_it_came_from(
        self, db_session, job, revenue_facts, context
    ):
        first, _, last = revenue_facts
        cagr(context, start=fact_quantity(first), end=fact_quantity(last), years=2)
        rows = await calculation_service.persist_context(db_session, context, job_id=job.id)

        tree = await calculation_service.lineage(db_session, rows[0].id)
        leaf = next(node for node in tree.leaves if node.identifier == str(first.id))

        assert leaf.detail["accession"] == "0000789019-20-000039"
        assert leaf.detail["filed_date"] == "2020-07-30"
        assert leaf.detail["basis"] == "as_reported"

    async def test_the_root_carries_the_formula_and_code_version(
        self, db_session, job, revenue_facts, context
    ):
        first, _, last = revenue_facts
        cagr(context, start=fact_quantity(first), end=fact_quantity(last), years=2)
        rows = await calculation_service.persist_context(db_session, context, job_id=job.id)

        tree = await calculation_service.lineage(db_session, rows[0].id)

        assert tree.detail["formula"].startswith("cagr =")
        assert tree.detail["code_version"] == "a1b2c3d4"
        assert tree.detail["parameters"] == {"years": 2}

    async def test_a_chained_calculation_produces_a_nested_tree(
        self, db_session, job, revenue_facts, context
    ):
        first, middle, last = revenue_facts
        early = growth_rate(context, start=fact_quantity(first), end=fact_quantity(middle))
        late = growth_rate(context, start=fact_quantity(middle), end=fact_quantity(last))
        ratio(context, numerator=late, denominator=early)

        rows = await calculation_service.persist_context(db_session, context, job_id=job.id)
        tree = await calculation_service.lineage(db_session, rows[2].id)

        assert tree.kind == "calculation"
        assert {child.kind for child in tree.inputs} == {"calculation"}
        assert {node.kind for node in tree.leaves} == {"fact"}

    async def test_an_assumption_appears_as_a_leaf_with_its_justification(
        self, db_session, job, request_row, context
    ):
        assumption = Assumption(
            request_id=request_row.id,
            name="terminal_growth",
            value=Decimal("0.025"),
            unit="pure",
            justification="Long-run nominal GDP growth for the US, per the CBO projection.",
            confidence=0.6,
            proposed_by="analysis",
        )
        db_session.add(assumption)
        await db_session.flush()

        ratio(
            context,
            numerator=usd(100),
            denominator=money(
                4, "USD", source=SourceRef.assumption(assumption.id, label="terminal_growth")
            ),
        )
        rows = await calculation_service.persist_context(db_session, context, job_id=job.id)

        tree = await calculation_service.lineage(db_session, rows[0].id)
        leaf = next(node for node in tree.leaves if node.kind == "assumption")

        assert "nominal GDP" in leaf.detail["justification"]
        assert leaf.detail["approved"] is False

    async def test_a_dangling_reference_is_reported_not_hidden(self, db_session, job, context):
        # An input pointing at a deleted fact is a real problem with the report that cites
        # it. A tree that silently omitted it would render as though the chain were whole.
        missing_id = uuid.uuid4()
        growth_rate(
            context,
            start=usd(100, SourceRef.financial_fact(missing_id)),
            end=usd(110, SourceRef.financial_fact(missing_id)),
        )
        rows = await calculation_service.persist_context(db_session, context, job_id=job.id)

        tree = await calculation_service.lineage(db_session, rows[0].id)

        assert all(node.kind == "missing" for node in tree.leaves)
        assert all(not node.is_resolved for node in tree.leaves)
        # Named against the relation that was searched, not against the kind. "No row in
        # financial_facts" is checkable; "no fact" was a statement about a lookup nobody
        # could see, and it read the same whether the row was deleted or the id had never
        # lived in that table at all (ADR 0072).
        assert tree.leaves[0].detail["expected"] == "financial_facts"

    async def test_lineage_of_an_unknown_calculation_raises(self, db_session):
        with pytest.raises(ValidationError, match="No calculation"):
            await calculation_service.lineage(db_session, uuid.uuid4())

    async def test_the_tree_serialises(self, db_session, job, revenue_facts, context):
        first, _, last = revenue_facts
        cagr(context, start=fact_quantity(first), end=fact_quantity(last), years=2)
        rows = await calculation_service.persist_context(db_session, context, job_id=job.id)

        payload = (await calculation_service.lineage(db_session, rows[0].id)).as_dict()

        assert payload["kind"] == "calculation"
        assert payload["detail"]["formula"].startswith("cagr =")
        assert len(payload["inputs"]) == 2

    async def test_every_calculation_a_job_made_can_be_listed(self, db_session, job, context):
        growth_rate(context, start=usd(100), end=usd(110))
        margin(context, part=usd(30), whole=usd(100))
        await calculation_service.persist_context(db_session, context, job_id=job.id)

        listed = await calculation_service.calculations_for_job(db_session, job.id)

        assert {row.name for row in listed} == {"growth_rate", "margin"}


class TestTheLeafRegistry:
    """Both ends of ADR 0072's registry, checked against each other.

    A source reference names the relation it resolves against, and resolution is a mapping
    from that name to a loader. Two lists only behave as one registry if something asserts
    they agree — otherwise a constructor can mint a table nobody reads, which is the defect
    this decision closed wearing different clothes.
    """

    def test_every_relation_a_leaf_can_name_has_a_loader(self):
        registered = set(calculation_service._LEAF_LOADERS)
        # Calculations resolve through their own path: they are the one kind with children,
        # so the walk continues through them rather than stopping at a leaf.
        expected = set(SourceTable) - {SourceTable.CALCULATIONS}

        assert registered == expected

    def test_a_calculation_is_deliberately_not_a_leaf(self):
        assert SourceTable.CALCULATIONS not in calculation_service._LEAF_LOADERS

    def test_every_constructor_names_a_relation(self):
        made = (
            SourceRef.financial_fact("a"),
            SourceRef.macro_observation("b"),
            SourceRef.security("c"),
            SourceRef.assumption("d"),
            SourceRef.calculation("e"),
        )

        assert {ref.table for ref in made} == set(SourceTable)

    def test_the_three_relations_that_carry_a_fact_are_the_legacy_candidates(self):
        # A row written before the table was recorded is resolved by trying the relations
        # its kind was ever minted over. If a fourth relation ever carries a FACT, this is
        # the test that notices the compatibility walk was not told.
        carry_a_fact = {
            ref.table
            for ref in (
                SourceRef.financial_fact("a"),
                SourceRef.macro_observation("b"),
                SourceRef.security("c"),
            )
        }

        candidates = set(calculation_service._LEGACY_CANDIDATES[SourceKind.FACT.value])

        assert candidates == carry_a_fact

    def test_a_stored_input_without_a_table_is_a_legacy_row_not_a_broken_one(self):
        stored = calculation_service._StoredInput.of(
            {"name": "revenue", "value": "1", "unit": "USD", "source": {"kind": "fact", "id": "x"}}
        )

        assert stored.table == ""


@pytest.mark.integration
class TestLeavesResolveByRelation:
    """The defect ADR 0072 closed: a leaf found by the relation it actually lives in."""

    async def test_a_macro_observation_resolves_instead_of_dangling(self, db_session, job, context):
        series = MacroSeriesRow(
            key="UK.BANKRATE",
            provider=Provider.FRED,
            identifier="BANKRATE",
            dataset="",
            label="Bank Rate",
            unit="percent",
            frequency="M",
            originator="Bank of England",
            licence_note="Open Government Licence v3.0",
        )
        db_session.add(series)
        await db_session.flush()
        observation = MacroObservationRow(
            series_id=series.id,
            observed_on=date(2024, 6, 30),
            vintage=date(2024, 7, 15),
            value=Decimal("5.25"),
            is_archived=True,
        )
        db_session.add(observation)
        await db_session.flush()

        growth_rate(
            context,
            start=money(100, "USD", source=SourceRef.macro_observation(observation.id)),
            end=money(110, "USD", source=SourceRef.macro_observation(observation.id)),
        )
        rows = await calculation_service.persist_context(db_session, context, job_id=job.id)

        tree = await calculation_service.lineage(db_session, rows[0].id)

        assert all(node.kind == "fact" for node in tree.leaves)
        assert all(node.is_resolved for node in tree.leaves)
        assert tree.leaves[0].detail["table"] == "macro_observations"
        assert tree.leaves[0].detail["series"] == "UK.BANKRATE"
        # The vintage is half of what identifies a macro figure: an archive restates, so
        # the period alone does not say which number this was.
        assert tree.leaves[0].detail["vintage"] == "2024-07-15"

    async def test_a_security_resolves_instead_of_dangling(self, db_session, job, context):
        security = Security(
            ticker="BARC",
            exchange="LSE",
            provider_symbol="BARC.LSE",
            quote_currency="GBX",
        )
        db_session.add(security)
        await db_session.flush()

        growth_rate(
            context,
            start=money(100, "GBP", source=SourceRef.security(security.id)),
            end=money(110, "GBP", source=SourceRef.security(security.id)),
        )
        rows = await calculation_service.persist_context(db_session, context, job_id=job.id)

        tree = await calculation_service.lineage(db_session, rows[0].id)

        assert all(node.is_resolved for node in tree.leaves)
        assert tree.leaves[0].detail["table"] == "securities"
        # GBX rather than GBP is what says the figure above needed the pence conversion at
        # all, so it is part of what the leaf has to show.
        assert tree.leaves[0].detail["quote_currency"] == "GBX"

    async def test_a_row_written_before_the_table_was_recorded_still_resolves(
        self, db_session, job, revenue_facts, context
    ):
        # The compatibility walk. Rows persisted before ADR 0072 carry a kind and no table,
        # and must keep resolving: the decision was to stop guessing, not to orphan what the
        # guess had already written.
        first, _, last = revenue_facts
        cagr(context, start=fact_quantity(first), end=fact_quantity(last), years=2)
        rows = await calculation_service.persist_context(db_session, context, job_id=job.id)

        legacy = []
        for stored in rows[0].inputs:
            entry = dict(stored)
            entry["source"] = {k: v for k, v in entry["source"].items() if k != "table"}
            legacy.append(entry)
        rows[0].inputs = legacy
        await db_session.flush()

        tree = await calculation_service.lineage(db_session, rows[0].id)

        assert all(node.is_resolved for node in tree.leaves)
        assert {node.identifier for node in tree.leaves} == {str(first.id), str(last.id)}

    async def test_a_relation_this_build_does_not_know_is_named_in_the_failure(
        self, db_session, job, context
    ):
        # A row from a newer schema, or a corrupt one. It resolves to nothing either way,
        # and the point is that the viewer says which relation it could not read rather
        # than reporting the kind and leaving the reader to guess what was searched.
        growth_rate(context, start=usd(100), end=usd(110))
        rows = await calculation_service.persist_context(db_session, context, job_id=job.id)
        rewritten = []
        for stored in rows[0].inputs:
            entry = dict(stored)
            entry["source"] = {**entry["source"], "table": "attestations", "id": str(uuid.uuid4())}
            rewritten.append(entry)
        rows[0].inputs = rewritten
        await db_session.flush()

        tree = await calculation_service.lineage(db_session, rows[0].id)

        assert all(node.kind == "missing" for node in tree.leaves)
        assert tree.leaves[0].detail["expected"] == "attestations"
