"""``GET /api/calculations/{id}``: provenance, from outside the process.

The endpoint that makes the platform's central claim checkable by someone who does not
trust the code. It has to return the formula, every input with its unit and source, the
code version, and the lineage down to evidence.

These tests commit for real, because the application under test runs its own session and
cannot see an uncommitted transaction. The truncation fixture is what stops one test's
rows deciding another test's result.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from aer.calc.basic import cagr, growth_rate, margin
from aer.calc.engine import CalculationContext
from aer.calc.units import SourceRef, money
from aer.core.enums import (
    FactBasis,
    JobStatus,
    Provider,
    RequestStatus,
    SourceTier,
    UserRole,
)
from aer.db.models import (
    Artefact,
    Company,
    FinancialFact,
    Job,
    ResearchRequest,
    SourceDocument,
    User,
)
from aer.services import calculations as calculation_service
from tests.api_fixtures import build_app, client_for
from tests.sec_fixtures import MSFT_CIK

pytestmark = pytest.mark.integration

ENDPOINT = "/api/calculations"


@pytest.fixture
async def clean_slate(db_engine):
    """Empty everything these tests write, before each one.

    The application commits for real, so its writes outlive the test that made them.
    Truncated at setup rather than teardown: it is what the *next* test needs, and doing
    it here cannot contend with a transaction a finished test still holds open.
    """
    async with db_engine.begin() as connection:
        await connection.execute(text("SET LOCAL statement_timeout = '5s'"))
        await connection.execute(
            text(
                "TRUNCATE calculations, assumptions, financial_facts, companies, "
                "source_documents, artefacts, jobs, research_requests, audit_events, "
                "users RESTART IDENTITY CASCADE"
            )
        )


@pytest.fixture
async def committed(clean_slate, db_engine):
    """A job and two revenue facts, committed so the application's session can see them."""
    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    async with factory() as session:
        user = User(email="calc-api@example.invalid", display_name="Calc API", role=UserRole.OWNER)
        session.add(user)
        await session.flush()

        request = ResearchRequest(
            user_id=user.id,
            company_name="Microsoft Corporation",
            ticker="MSFT",
            exchange="NASDAQ",
            as_of_date=date(2023, 1, 1),
            base_currency="USD",
            investment_horizon_months=36,
            max_cost_gbp="2.00",
            portfolio_context={},
            status=RequestStatus.DRAFT,
        )
        company = Company(name="MICROSOFT CORP", cik=MSFT_CIK, ticker="MSFT", exchange="NASDAQ")
        session.add_all([request, company])
        await session.flush()

        job = Job(
            request_id=request.id,
            workflow_version="test-1",
            code_version="a1b2c3d4",
            status=JobStatus.RUNNING,
        )
        session.add(job)
        await session.flush()
        await session.commit()

        return {"job": job, "request": request, "company": company}


async def persist(db_engine, job_id, build):
    """Run ``build`` against a fresh context and commit the result.

    Returns the persisted rows. Committed rather than flushed, because the API runs in a
    different session and would otherwise see nothing.
    """
    context = CalculationContext(code_version="a1b2c3d4")
    build(context)
    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    async with factory() as session:
        rows = await calculation_service.persist_context(session, context, job_id=job_id)
        await session.commit()
        return rows


@pytest.fixture
async def api(api_settings, db_engine, fake_redis, committed):
    async for client in client_for(build_app(api_settings, engine=db_engine, redis=fake_redis)):
        yield client


def usd(value, source=None):
    return money(value, "USD", source=source or SourceRef.fact("fact-1"))


class TestReadingACalculation:
    async def test_it_returns_the_formula_inputs_and_code_version(self, api, db_engine, committed):
        rows = await persist(
            db_engine,
            committed["job"].id,
            lambda ctx: cagr(ctx, start=usd(100), end=usd(200), years=3),
        )

        response = await api.get(f"{ENDPOINT}/{rows[0].id}")

        assert response.status_code == 200
        body = response.json()["calculation"]
        assert body["formula"] == "cagr = (end / start) ^ (1 / years) - 1"
        assert body["function_ref"] == "aer.calc.basic:cagr"
        assert body["code_version"] == "a1b2c3d4"
        assert body["parameters"] == {"years": 3}

    async def test_every_input_carries_a_unit_and_a_source(self, api, db_engine, committed):
        rows = await persist(
            db_engine,
            committed["job"].id,
            lambda ctx: growth_rate(ctx, start=usd(100), end=usd(110)),
        )

        inputs = (await api.get(f"{ENDPOINT}/{rows[0].id}")).json()["calculation"]["inputs"]

        assert {i["name"] for i in inputs} == {"start", "end"}
        assert all(i["unit"] == "USD" for i in inputs)
        assert all(i["source"]["kind"] == "fact" for i in inputs)

    async def test_the_output_value_is_a_string_not_a_json_number(self, api, db_engine, committed):
        # JSON numbers are IEEE doubles in every parser that will consume this.
        # Serialising an exact Decimal as one would corrupt the figure at the boundary --
        # the last place anybody would look for a rounding error.
        rows = await persist(
            db_engine,
            committed["job"].id,
            lambda ctx: margin(ctx, part=usd("44281000000"), whole=usd("143015000000")),
        )

        body = (await api.get(f"{ENDPOINT}/{rows[0].id}")).json()["calculation"]

        assert isinstance(body["output_value"], str)
        assert Decimal(body["output_value"]) == Decimal("0.309624864525")

    async def test_the_declared_assumptions_are_returned(self, api, db_engine, committed):
        rows = await persist(
            db_engine,
            committed["job"].id,
            lambda ctx: cagr(ctx, start=usd(100), end=usd(200), years=3),
        )

        body = (await api.get(f"{ENDPOINT}/{rows[0].id}")).json()["calculation"]

        assert any("equal length" in note for note in body["assumptions"])

    async def test_an_unknown_calculation_is_a_problem_details_404(self, api):
        response = await api.get(f"{ENDPOINT}/{uuid.uuid4()}")

        assert response.status_code == 404
        assert response.headers["content-type"].startswith("application/problem+json")
        body = response.json()
        assert body["code"] == "calculation_not_found"
        assert body["type"].endswith("calculation_not_found")


class TestTheLineageTree:
    @pytest.fixture
    async def with_facts(self, db_engine, committed):
        """Two revenue facts, committed, plus the source document they came from."""
        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as session:
            artefact = Artefact(
                sha256="a" * 64,
                media_type="application/json",
                size_bytes=100,
                storage_backend="local",
                storage_key="aa/aa/" + "a" * 64,
            )
            session.add(artefact)
            await session.flush()

            document = SourceDocument(
                request_id=committed["request"].id,
                artefact_id=artefact.id,
                url="https://data.sec.gov/api/xbrl/companyfacts/CIK0000789019.json",
                provider=Provider.SEC_EDGAR,
                source_tier=SourceTier.T1_REGULATORY,
                retrieved_at=date(2023, 1, 1),
                quarantined=False,
            )
            session.add(document)
            await session.flush()

            facts = [
                FinancialFact(
                    company_id=committed["company"].id,
                    source_document_id=document.id,
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
                    (2022, "198270000000", "0000789019-22-000010"),
                )
            ]
            session.add_all(facts)
            await session.commit()
            return facts

    async def test_the_tree_resolves_down_to_the_facts(self, api, db_engine, committed, with_facts):
        first, last = with_facts
        rows = await persist(
            db_engine,
            committed["job"].id,
            lambda ctx: cagr(
                ctx,
                start=money(first.value, "USD", source=SourceRef.fact(first.id)),
                end=money(last.value, "USD", source=SourceRef.fact(last.id)),
                years=2,
            ),
        )

        body = (await api.get(f"{ENDPOINT}/{rows[0].id}")).json()

        leaves = body["lineage"]["inputs"]
        assert {leaf["kind"] for leaf in leaves} == {"fact"}
        assert {leaf["id"] for leaf in leaves} == {str(first.id), str(last.id)}
        assert body["unresolved"] == []

    async def test_a_leaf_shows_the_filing_it_came_from(
        self, api, db_engine, committed, with_facts
    ):
        first, last = with_facts
        rows = await persist(
            db_engine,
            committed["job"].id,
            lambda ctx: cagr(
                ctx,
                start=money(first.value, "USD", source=SourceRef.fact(first.id)),
                end=money(last.value, "USD", source=SourceRef.fact(last.id)),
                years=2,
            ),
        )

        body = (await api.get(f"{ENDPOINT}/{rows[0].id}")).json()
        leaf = next(n for n in body["lineage"]["inputs"] if n["id"] == str(first.id))

        assert leaf["detail"]["accession"] == "0000789019-20-000039"
        assert leaf["detail"]["filed_date"] == "2020-07-30"

    async def test_the_tree_can_be_omitted(self, api, db_engine, committed):
        rows = await persist(
            db_engine,
            committed["job"].id,
            lambda ctx: margin(ctx, part=usd(30), whole=usd(100)),
        )

        body = (await api.get(f"{ENDPOINT}/{rows[0].id}?include_lineage=false")).json()

        assert body["lineage"] is None
        assert body["calculation"]["name"] == "margin"

    async def test_unresolved_references_are_reported_at_the_top_level(
        self, api, db_engine, committed
    ):
        # A headline about the report, not a detail buried in one branch of the tree.
        missing = SourceRef.fact(uuid.uuid4())
        rows = await persist(
            db_engine,
            committed["job"].id,
            lambda ctx: growth_rate(ctx, start=usd(100, missing), end=usd(110, missing)),
        )

        body = (await api.get(f"{ENDPOINT}/{rows[0].id}")).json()

        assert len(body["unresolved"]) == 2
        assert all(entry["expected"] == "fact" for entry in body["unresolved"])
