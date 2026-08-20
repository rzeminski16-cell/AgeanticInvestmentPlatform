"""The comps table with peers actually in it, and the band their range implies.

Gap S3. The table has been the subject's row plus a list of exclusions since it first
rendered, because a peer's multiple needs that peer's price and nothing ever fetched one.
These tests pin the filled table: each confirmed peer priced through the same archived,
licensed path as the subject, its multiples computed from its own filed statements, the
subject carrying the full multiple set — P/E, P/B, EV/EBITDA, EV/Sales — and the peers'
range read back onto the subject as a recorded implied-value band.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.calc import comps as calc
from aer.core.enums import Decision, GateKind, JobStatus, Provider, SourceTier, UserRole
from aer.core.hashing import canonical_json, sha256_hex
from aer.db.models import (
    Artefact,
    Company,
    FinancialFact,
    JobStep,
    ResearchRequest,
    SourceDocument,
    User,
)
from aer.services import approvals as approval_service
from aer.services import comps as comps_service
from aer.services.analysis import analyse_company
from aer.services.calculations import new_context
from aer.services.comps_run import CompsOutcome, build_comps_table, grouped_exclusions
from aer.services.price_acquisition import acquire_prices
from aer.storage.local import LocalArtefactStore
from tests.test_price_acquisition import StubPriceClient
from tests.workflow_fixtures import seed_job

pytestmark = pytest.mark.integration

AS_OF = date(2024, 6, 28)
PERIOD_END = date(2024, 3, 31)

# What each company filed, in the concepts the statement assembler places. Enough for the
# full multiple set: EBITDA derives from operating income and depreciation, the per-share
# denominators from the filed EPS and the equity over the count.
SUBJECT_FIGURES: dict[str, tuple[str, str]] = {
    "revenue": ("10000", "USD"),
    "operating_income": ("3000", "USD"),
    "depreciation_and_amortisation": ("500", "USD"),
    "net_income": ("2000", "USD"),
    "earnings_per_share_diluted": ("3", "USD/shares"),
    "equity": ("4000", "USD"),
    "shares_outstanding": ("100", "shares"),
    "cash_and_equivalents": ("1000", "USD"),
    "total_debt": ("2000", "USD"),
}

PEER_FIGURES: dict[str, tuple[str, str]] = {
    "revenue": ("5000", "USD"),
    "operating_income": ("1500", "USD"),
    "depreciation_and_amortisation": ("250", "USD"),
    "net_income": ("1000", "USD"),
    "earnings_per_share_diluted": ("2", "USD/shares"),
    "equity": ("500", "USD"),
    "shares_outstanding": ("50", "shares"),
    "cash_and_equivalents": ("100", "USD"),
    "total_debt": ("200", "USD"),
}


@pytest.fixture
async def scene(db_session: AsyncSession, tmp_path: Any) -> dict[str, Any]:
    user = User(email="comps@example.invalid", display_name="C", role=UserRole.ANALYST)
    db_session.add(user)
    await db_session.flush()

    request = ResearchRequest(
        user_id=user.id,
        company_name="Contoso Corporation",
        ticker="CTSO",
        exchange="NASDAQ",
        as_of_date=AS_OF,
        point_in_time=True,
        base_currency="USD",
        reporting_currency="USD",
        investment_horizon_months=12,
        max_cost_gbp="2.50",
    )
    db_session.add(request)
    await db_session.flush()

    job = await seed_job(db_session, request=request)

    subject = Company(
        name="Contoso Corporation", cik="0000000011", ticker="CTSO", exchange="NASDAQ"
    )
    peer = Company(name="Fabrikam Inc", cik="0000000012", ticker="FBRK", exchange="NASDAQ")
    db_session.add_all([subject, peer])
    await db_session.flush()

    await _file_figures(db_session, request=request, company=subject, figures=SUBJECT_FIGURES)
    await _file_figures(db_session, request=request, company=peer, figures=PEER_FIGURES)

    store = LocalArtefactStore(tmp_path / "artefacts", max_bytes=50_000_000)
    return {
        "session": db_session,
        "request": request,
        "job": job,
        "user": user,
        "subject": subject,
        "peer": peer,
        "store": store,
    }


async def _file_figures(
    session: AsyncSession,
    *,
    request: ResearchRequest,
    company: Company,
    figures: dict[str, tuple[str, str]],
) -> None:
    """One fiscal year of facts for one company, behind a real archived document."""
    artefact = Artefact(
        sha256=f"{company.cik[-1]}" * 64,
        media_type="application/json",
        size_bytes=64,
        storage_key=f"test/{company.ticker}",
    )
    session.add(artefact)
    await session.flush()

    document = SourceDocument(
        artefact_id=artefact.id,
        request_id=request.id,
        provider=Provider.SEC_EDGAR,
        source_tier=SourceTier.T1_REGULATORY,
        url=f"https://sec.gov/{company.ticker}",
        retrieved_at=datetime.now(UTC),
    )
    session.add(document)
    await session.flush()

    for concept, (value, unit) in figures.items():
        session.add(
            FinancialFact(
                company_id=company.id,
                source_document_id=document.id,
                concept=concept,
                value=Decimal(value),
                unit=unit,
                period_start=None if unit == "shares" else date(2023, 4, 1),
                period_end=PERIOD_END,
                fiscal_period="FY",
                fiscal_year=2024,
                filed_date=date(2024, 5, 1),
            )
        )
    await session.flush()


async def _confirm_peers(session: AsyncSession, scene: dict[str, Any]) -> None:
    """A proposal step and its approvals, the way the workflow records them."""
    output: dict[str, Any] = {
        "subject": str(scene["subject"].id),
        "subject_period_end": PERIOD_END.isoformat(),
        "basis": "lfy",
        "proposed_by": "test",
        "peers": [
            {
                "identifier": str(scene["peer"].id),
                "name": scene["peer"].name,
                "rationale": "Same industry group.",
                "period_end": PERIOD_END.isoformat(),
            }
        ],
    }
    session.add(
        JobStep(
            job_id=scene["job"].id,
            step_key=comps_service.PEER_SET_STEP,
            sequence=4,
            status=JobStatus.SUCCEEDED,
            idempotency_key=f"{scene['job'].id}:{comps_service.PEER_SET_STEP}",
            input_hash="0" * 64,
            output_ref=output,
        )
    )
    await session.flush()

    await approval_service.record_decision(
        session,
        job=scene["job"],
        gate=GateKind.PLAN,
        decision=Decision.APPROVED,
        actor=scene["user"],
        payload_hash="1" * 64,
    )
    await approval_service.record_decision(
        session,
        job=scene["job"],
        gate=GateKind.PEER_SET,
        decision=Decision.APPROVED,
        actor=scene["user"],
        payload_hash=sha256_hex(canonical_json(comps_service.peer_set_payload(output))),
    )


async def _build(scene: dict[str, Any], *, client: Any, context: Any = None) -> tuple[Any, Any]:
    """Acquire the subject's prices when there is a client, then build the table."""
    session: AsyncSession = scene["session"]
    ledger = context if context is not None else new_context()

    capitalisation = None
    if client is not None:
        acquired = await acquire_prices(
            session,
            client,
            scene["store"],
            request=scene["request"],
            company=scene["subject"],
            job_id=scene["job"].id,
            context=new_context(),
        )
        capitalisation = acquired.market_capitalisation

    analysis = await analyse_company(
        session, new_context(), company_id=scene["subject"].id, request=scene["request"]
    )
    outcome = await build_comps_table(
        session,
        ledger,
        job=scene["job"],
        request=scene["request"],
        company_name=scene["request"].company_name,
        ticker=scene["request"].ticker,
        analysis=analysis,
        market_capitalisation=capitalisation,
        as_of=AS_OF,
        client=client,
        store=scene["store"] if client is not None else None,
    )
    return outcome, ledger


def _present(row_set: Any) -> dict[str, str]:
    return {
        row.key: str(row.quantity.value) for row in row_set.multiples if row.quantity is not None
    }


class TestTheSubjectRow:
    async def test_the_subject_carries_the_full_multiple_set(self, scene: dict[str, Any]) -> None:
        """P/E, P/B, EV/EBITDA and EV/Sales, each from the subject's own filed figures and
        its own stored close — the 'full multiples' decision, as a table row."""
        await _confirm_peers(scene["session"], scene)
        client = StubPriceClient(scene["store"])

        outcome, _ = await _build(scene, client=client)

        assert outcome.built
        present = _present(outcome.table.subject)
        assert {"pe", "p_b", "ev_ebitda", "ev_sales"} <= present.keys()

    async def test_without_prices_each_absence_names_its_missing_input(
        self, scene: dict[str, Any]
    ) -> None:
        await _confirm_peers(scene["session"], scene)

        outcome, _ = await _build(scene, client=None)

        assert outcome.built
        by_key = {row.key: row for row in outcome.table.subject.multiples}
        assert by_key["pe"].quantity is None
        assert "price_per_share" in by_key["pe"].absent_because


class TestThePeerRows:
    async def test_a_confirmed_peer_gains_a_row_of_its_own_multiples(
        self, scene: dict[str, Any]
    ) -> None:
        """The peer's statements are its own filed facts; the price is fetched and
        archived exactly as the subject's. Nothing in its row is the subject's data."""
        await _confirm_peers(scene["session"], scene)
        client = StubPriceClient(scene["store"])

        outcome, _ = await _build(scene, client=client)

        assert [peer.name for peer in outcome.table.peers] == ["Fabrikam Inc"]
        present = _present(outcome.table.peers[0])
        assert {"pe", "p_b", "ev_ebitda", "ev_sales"} <= present.keys()
        # Its own figures, not the subject's: same stub close, half the revenue.
        assert present["ev_sales"] != _present(outcome.table.subject)["ev_sales"]

    async def test_the_peer_price_is_archived_with_its_licence(self, scene: dict[str, Any]) -> None:
        """Invariant 1 does not relax for a peer: the bars behind its multiples are an
        artefact with a digest and a licence note, exactly as the subject's are."""
        await _confirm_peers(scene["session"], scene)
        client = StubPriceClient(scene["store"])

        await _build(scene, client=client)

        rows = list(
            await scene["session"].scalars(
                select(SourceDocument).where(
                    SourceDocument.provider == Provider.EODHD,
                    SourceDocument.title.contains("Fabrikam"),
                )
            )
        )
        assert rows
        assert all(row.source_tier == SourceTier.T4_LICENSED_MARKET for row in rows)

    async def test_without_a_subscription_every_peer_is_excluded_by_name(
        self, scene: dict[str, Any]
    ) -> None:
        """The pre-S3 behaviour, kept as the degraded mode: no client means no peer
        price, and a table that says so beats one that silently shrinks."""
        await _confirm_peers(scene["session"], scene)

        outcome, _ = await _build(scene, client=None)

        assert outcome.built
        assert outcome.table.peers == ()
        assert [item.name for item in outcome.table.excluded] == ["Fabrikam Inc"]
        assert "price series" in outcome.table.excluded[0].reason


class TestTheImpliedBand:
    async def test_the_peer_range_is_read_onto_the_subject_as_recorded_values(
        self, scene: dict[str, Any]
    ) -> None:
        """The band's ends are traced calculations in the run's ledger — the enterprise
        multiple applied to the subject's own EBITDA, bridged and spread per share — so
        the football field's comps band resolves to rows like any other figure."""
        await _confirm_peers(scene["session"], scene)
        client = StubPriceClient(scene["store"])

        outcome, ledger = await _build(scene, client=client)

        assert outcome.band is not None
        assert outcome.band["label"] == "Comps (EV/EBITDA)"
        assert Decimal(outcome.band["low"]) <= Decimal(outcome.band["high"])
        assert outcome.band["unit"] == "USD/shares"
        recorded = [
            record
            for record in ledger.records
            if record.name == "implied_value_per_share_from_ev_multiple"
        ]
        assert len(recorded) == 2

    async def test_no_priced_peer_means_no_band(self, scene: dict[str, Any]) -> None:
        await _confirm_peers(scene["session"], scene)

        outcome, _ = await _build(scene, client=None)

        assert outcome.band is None
        assert outcome.as_dict()["comps_band"] is None


class TestTheStepOutput:
    async def test_the_peer_multiples_reach_the_output(self, scene: dict[str, Any]) -> None:
        """The comparables page and the internal field read the step output, so the
        peers' rows have to survive serialisation — values as strings, absences named."""
        await _confirm_peers(scene["session"], scene)
        client = StubPriceClient(scene["store"])

        outcome, _ = await _build(scene, client=client)
        body = outcome.as_dict()

        assert body["peers"] == 1
        [peer] = body["peer_multiples"]
        assert peer["name"] == "Fabrikam Inc"
        assert any(row["value"] is not None for row in peer["multiples"])

    def test_the_excluded_count_is_of_companies_not_of_grouped_rows(self) -> None:
        """The render-time disclosure reads its counts from this record (gap A53), and a
        grouped row joins several names into one string, so the rows cannot be counted
        back — the company count travels alongside them."""
        table = calc.CompsTable(
            subject=calc.PeerRow(
                identifier="SUBJ", name="Subject", period_end=PERIOD_END, multiples=()
            ),
            peers=(),
            excluded=(
                calc.PeerExclusion(
                    identifier="alpha",
                    name="Alpha plc",
                    period_end=AS_OF,
                    reason=comps_service.UNACQUIRED_PEER_REASON,
                ),
                calc.PeerExclusion(
                    identifier="beta",
                    name="Beta Inc",
                    period_end=AS_OF,
                    reason=comps_service.UNACQUIRED_PEER_REASON,
                ),
            ),
            basis=calc.MultipleBasis.LAST_FISCAL_YEAR,
            as_of=AS_OF,
            peer_set_confirmed=True,
        )

        body = CompsOutcome(built=True, table=table).as_dict()

        assert body["excluded_count"] == 2
        assert len(body["excluded"]) == 1


class TestTheExclusionsAreGroupedByReason:
    """Eight peers excluded for the same one reason must not be eight repeated paragraphs.

    The first complete run's approval page said the identical sentence eight times, once
    per peer. The output states each distinct reason once, naming together the peers it
    covers, in the order the reasons first appeared.
    """

    @staticmethod
    def _excluded(name: str, reason: str) -> calc.PeerExclusion:
        return calc.PeerExclusion(
            identifier=name.lower(), name=name, period_end=AS_OF, reason=reason
        )

    def test_one_shared_reason_becomes_one_row_naming_everyone(self) -> None:
        rows = grouped_exclusions(
            [
                self._excluded("Alpha plc", comps_service.UNACQUIRED_PEER_REASON),
                self._excluded("Beta Inc", comps_service.UNACQUIRED_PEER_REASON),
                self._excluded("Gamma SE", comps_service.UNACQUIRED_PEER_REASON),
            ]
        )

        assert rows == [
            {
                "name": "Alpha plc, Beta Inc, Gamma SE",
                "reason": comps_service.UNACQUIRED_PEER_REASON,
            }
        ]

    def test_distinct_reasons_keep_their_own_rows_in_first_seen_order(self) -> None:
        rows = grouped_exclusions(
            [
                self._excluded("Alpha plc", "reporting period too far from the subject's"),
                self._excluded("Beta Inc", comps_service.UNACQUIRED_PEER_REASON),
                self._excluded("Gamma SE", "reporting period too far from the subject's"),
            ]
        )

        assert [row["reason"] for row in rows] == [
            "reporting period too far from the subject's",
            comps_service.UNACQUIRED_PEER_REASON,
        ]
        assert rows[0]["name"] == "Alpha plc, Gamma SE"
        assert rows[1]["name"] == "Beta Inc"
