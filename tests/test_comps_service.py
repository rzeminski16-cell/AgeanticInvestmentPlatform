"""The peer set against the database: proposed, confirmed, and only then compared.

Task 30's acceptance criterion is that **no comps table is produced without a confirmed peer
set**, and `TestNoTableWithoutAConfirmedSet` is that criterion.

Falling through to an unconfirmed table is the tempting implementation and the wrong one, for
the same reason the sector gate refuses rather than defaulting to "unclassified": an empty
table and a withheld one read identically, and a table that exists is a table that gets read.
A badly chosen peer moves a median more than most modelling choices do, and it does it
invisibly — the multiple is computed correctly from a company that should not have been in the
set.
"""

from __future__ import annotations

import inspect
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import text

from aer.calc import comps as calc
from aer.calc.engine import CalculationContext
from aer.calc.units import DIMENSIONLESS, Quantity, SourceRef
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
from aer.fetch.policy import DEFAULT_POLICIES
from aer.render.document import assemble_document
from aer.render.markdown import _comps_block, render_markdown
from aer.services import approvals as approval_service
from aer.services import comps as service
from tests.workflow_fixtures import AS_OF_DATE, seed_job

pytestmark = pytest.mark.integration

_TABLES = "research_requests, audit_events, users, artefacts, prompts, companies"

PERIOD_END = date(2024, 6, 30)
AS_OF = date(2024, 6, 28)
SOURCE = SourceRef.fact("test-fact")


def pure(value: str) -> Quantity:
    return Quantity.of(Decimal(value), DIMENSIONLESS, source=SOURCE)


def result(key: str, value: str | None, *, absent: str = "") -> calc.MultipleResult:
    return calc.MultipleResult(
        key=key,
        label=key.upper(),
        quantity=pure(value) if value is not None else None,
        basis=calc.MultipleBasis.TRAILING_TWELVE_MONTHS,
        period_end=PERIOD_END,
        absent_because=absent,
    )


def subject_row() -> calc.PeerRow:
    return calc.PeerRow(
        identifier="SUBJ",
        name="Subject plc",
        period_end=PERIOD_END,
        multiples=(result("ev_ebitda", "12"),),
    )


@pytest.fixture
async def scene(db_session: Any) -> dict[str, Any]:
    await db_session.execute(text(f"TRUNCATE {_TABLES} RESTART IDENTITY CASCADE"))

    analyst = User(email="analyst@example.invalid", display_name="Analyst", role=UserRole.ANALYST)
    db_session.add(analyst)
    await db_session.flush()

    request = ResearchRequest(
        user_id=analyst.id,
        company_name="Subject plc",
        ticker="SUBJ",
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

    job = await seed_job(db_session, request=request)
    return {"request": request, "job": job, "analyst": analyst}


@pytest.fixture
def context() -> CalculationContext:
    return CalculationContext(code_version="test")


async def record_proposal(
    session: Any,
    scene: dict[str, Any],
    *,
    peers: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Write a peer-proposal step the way the workflow writes one."""
    output: dict[str, Any] = {
        "subject": "SUBJ",
        "subject_period_end": PERIOD_END.isoformat(),
        "basis": calc.MultipleBasis.TRAILING_TWELVE_MONTHS.value,
        "proposed_by": "planner",
        "peers": peers
        if peers is not None
        else [
            {
                "identifier": "PEER1",
                "name": "Peer One plc",
                "rationale": "Same industry, similar revenue",
                "period_end": PERIOD_END.isoformat(),
            },
            {
                "identifier": "PEER2",
                "name": "Peer Two plc",
                "rationale": "Same end market",
                "period_end": PERIOD_END.isoformat(),
            },
        ],
    }
    session.add(
        JobStep(
            job_id=scene["job"].id,
            step_key=service.PEER_SET_STEP,
            sequence=4,
            status=JobStatus.SUCCEEDED,
            idempotency_key=f"{scene['job'].id}:{service.PEER_SET_STEP}",
            input_hash="0" * 64,
            output_ref=output,
        )
    )
    await session.flush()
    return output


async def approve_plan(session: Any, scene: dict[str, Any]) -> None:
    """Gates are passed in order, so the peer-set gate needs the plan gate behind it."""
    await approval_service.record_decision(
        session,
        job=scene["job"],
        gate=GateKind.PLAN,
        decision=Decision.APPROVED,
        actor=scene["analyst"],
        payload_hash="1" * 64,
    )


async def confirm(session: Any, scene: dict[str, Any], output: dict[str, Any]) -> None:
    await approve_plan(session, scene)
    await approval_service.record_decision(
        session,
        job=scene["job"],
        gate=GateKind.PEER_SET,
        decision=Decision.APPROVED,
        actor=scene["analyst"],
        payload_hash=sha256_hex(canonical_json(service.peer_set_payload(output))),
    )


async def seed_two_companies(
    session: Any, scene: dict[str, Any], *, subject_sic: str, peer_sic: str, peer_facts: bool = True
):
    """A subject and one candidate peer, with the peer's facts optional."""
    subject = Company(
        name="Subject plc", cik="0000000001", ticker="SUBJ", exchange="NASDAQ", sic=subject_sic
    )
    peer = Company(
        name="Peer plc", cik="0000000002", ticker="PEER", exchange="NASDAQ", sic=peer_sic
    )
    session.add_all([subject, peer])
    await session.flush()

    if peer_facts:
        artefact = Artefact(
            sha256="b" * 64,
            media_type="application/json",
            size_bytes=64,
            storage_key="sha256/bb/bb/" + "b" * 64,
        )
        session.add(artefact)
        await session.flush()

        document = SourceDocument(
            artefact_id=artefact.id,
            request_id=scene["request"].id,
            provider=Provider.SEC_EDGAR,
            source_tier=SourceTier.T1_REGULATORY,
            url="https://sec.gov/peer",
            retrieved_at=datetime.now(UTC),
        )
        session.add(document)
        await session.flush()

        session.add(
            FinancialFact(
                company_id=peer.id,
                source_document_id=document.id,
                concept="revenue",
                value=Decimal(100),
                unit="USD",
                period_end=PERIOD_END,
                filed_date=PERIOD_END,
            )
        )
        await session.flush()

    return subject, peer


# -- The criterion ------------------------------------------------------------------------------


class TestNoTableWithoutAConfirmedSet:
    """Task 30's acceptance criterion, stated as a test."""

    async def test_an_unconfirmed_set_refuses(self, db_session, scene):
        await record_proposal(db_session, scene)

        with pytest.raises(service.PeerSetNotConfirmedError) as excinfo:
            await service.build(
                db_session,
                scene["job"],
                subject=subject_row(),
                peer_multiples={},
                basis=calc.MultipleBasis.TRAILING_TWELVE_MONTHS,
                as_of=AS_OF,
            )
        assert "PEER_SET" in str(excinfo.value.context)

    async def test_it_refuses_rather_than_returning_an_empty_table(self, db_session, scene):
        """An empty table and a withheld one read identically and mean opposite things."""
        await record_proposal(db_session, scene)

        with pytest.raises(service.PeerSetNotConfirmedError):
            await service.confirmed_peer_set(db_session, scene["job"])

    async def test_a_confirmed_set_builds(self, db_session, scene):
        output = await record_proposal(db_session, scene)
        await confirm(db_session, scene, output)

        table = await service.build(
            db_session,
            scene["job"],
            subject=subject_row(),
            peer_multiples={
                "PEER1": (result("ev_ebitda", "10"),),
                "PEER2": (result("ev_ebitda", "11"),),
            },
            basis=calc.MultipleBasis.TRAILING_TWELVE_MONTHS,
            as_of=AS_OF,
        )

        assert table.peer_set_confirmed is True
        assert [row.identifier for row in table.peers] == ["PEER1", "PEER2"]
        assert table.median_of("ev_ebitda") == Decimal("10.5")

    async def test_a_run_that_proposed_no_peers_needs_no_gate(self, db_session, scene):
        """It has no comparison to defend and should not wait to confirm an empty list."""
        await record_proposal(db_session, scene, peers=[])

        assert await service.confirmed_peer_set(db_session, scene["job"]) == ()

    async def test_a_run_that_never_proposed_needs_no_gate(self, db_session, scene):
        assert await service.confirmed_peer_set(db_session, scene["job"]) == ()

    async def test_approving_one_set_does_not_approve_another(self, db_session, scene):
        """Somebody confirmed a different list of companies. That is not this approval."""
        output = await record_proposal(db_session, scene)
        await approve_plan(db_session, scene)
        await approval_service.record_decision(
            db_session,
            job=scene["job"],
            gate=GateKind.PEER_SET,
            decision=Decision.APPROVED,
            actor=scene["analyst"],
            payload_hash="9" * 64,
        )

        with pytest.raises(service.PeerSetNotConfirmedError) as excinfo:
            await service.confirmed_peer_set(db_session, scene["job"])
        assert "does not match" in str(excinfo.value)
        assert output["peers"]

    async def test_a_rejection_is_not_an_approval(self, db_session, scene):
        output = await record_proposal(db_session, scene)
        await approve_plan(db_session, scene)
        await approval_service.record_decision(
            db_session,
            job=scene["job"],
            gate=GateKind.PEER_SET,
            decision=Decision.REJECTED,
            actor=scene["analyst"],
            payload_hash=sha256_hex(canonical_json(service.peer_set_payload(output))),
        )

        with pytest.raises(service.PeerSetNotConfirmedError):
            await service.confirmed_peer_set(db_session, scene["job"])


class TestTheGatePayload:
    async def test_it_carries_every_peer_and_its_rationale(self, db_session, scene):
        await record_proposal(db_session, scene)

        payload = await service.gate_payload_for_job(db_session, scene["job"].id)

        assert [peer["name"] for peer in payload["peers"]] == ["Peer One plc", "Peer Two plc"]
        assert all(peer["rationale"] for peer in payload["peers"])

    async def test_a_run_that_has_not_proposed_gets_an_empty_payload(self, db_session, scene):
        """So a page renders "nothing to review" rather than an error."""
        assert await service.gate_payload_for_job(db_session, scene["job"].id) == {}

    async def test_a_peer_with_no_rationale_survives_to_the_reviewer(self, db_session, scene):
        """It is not silently dropped. A blank reason is what a reviewer needs to notice."""
        await record_proposal(
            db_session,
            scene,
            peers=[
                {
                    "identifier": "PEER1",
                    "name": "Unexplained plc",
                    "rationale": "",
                    "period_end": PERIOD_END.isoformat(),
                }
            ],
        )

        payload = await service.gate_payload_for_job(db_session, scene["job"].id)
        assert payload["peers"][0]["rationale"] == ""

    def test_a_proposal_with_peers_needs_the_gate(self):
        assert service.peer_set_required(
            {"peers": [{"identifier": "A", "name": "A", "rationale": "r", "period_end": "2024"}]}
        )

    def test_a_proposal_with_none_does_not(self):
        assert not service.peer_set_required({"peers": []})


# -- Exclusions ----------------------------------------------------------------------------------


class TestAnExcludedPeerIsRecordedNotDropped:
    async def test_a_drifted_year_end_is_excluded_with_a_reason(self, db_session, scene):
        drifted = (PERIOD_END - timedelta(days=180)).isoformat()
        output = await record_proposal(
            db_session,
            scene,
            peers=[
                {
                    "identifier": "PEER1",
                    "name": "Aligned plc",
                    "rationale": "Same industry",
                    "period_end": PERIOD_END.isoformat(),
                },
                {
                    "identifier": "PEER2",
                    "name": "March year-end plc",
                    "rationale": "Same industry",
                    "period_end": drifted,
                },
            ],
        )
        await confirm(db_session, scene, output)

        table = await service.build(
            db_session,
            scene["job"],
            subject=subject_row(),
            peer_multiples={
                "PEER1": (result("ev_ebitda", "10"),),
                "PEER2": (result("ev_ebitda", "30"),),
            },
            basis=calc.MultipleBasis.TRAILING_TWELVE_MONTHS,
            as_of=AS_OF,
        )

        assert [row.identifier for row in table.peers] == ["PEER1"]
        assert [row.identifier for row in table.excluded] == ["PEER2"]
        assert "days from the subject" in table.excluded[0].reason

    async def test_a_peer_with_no_computable_multiple_is_excluded_with_a_reason(
        self, db_session, scene
    ):
        """A table that silently shrinks is one whose median moved for invisible reasons."""
        output = await record_proposal(db_session, scene)
        await confirm(db_session, scene, output)

        table = await service.build(
            db_session,
            scene["job"],
            subject=subject_row(),
            peer_multiples={"PEER1": (result("ev_ebitda", "10"),)},
            basis=calc.MultipleBasis.TRAILING_TWELVE_MONTHS,
            as_of=AS_OF,
        )

        assert [row.identifier for row in table.peers] == ["PEER1"]
        assert [row.identifier for row in table.excluded] == ["PEER2"]
        assert "no multiple could be computed" in table.excluded[0].reason

    async def test_the_rationale_travels_with_the_peer(self, db_session, scene):
        output = await record_proposal(db_session, scene)
        await confirm(db_session, scene, output)

        table = await service.build(
            db_session,
            scene["job"],
            subject=subject_row(),
            peer_multiples={"PEER1": (result("ev_ebitda", "10"),)},
            basis=calc.MultipleBasis.TRAILING_TWELVE_MONTHS,
            as_of=AS_OF,
        )

        assert table.peers[0].rationale == "Same industry, similar revenue"


# -- The licence -----------------------------------------------------------------------------


class TestTheLicenceTravelsWithTheTable:
    """What may be published is read off the provider's policy, never decided here.

    `aer.calc.comps` is pure and may not consult a policy table, so the determination has
    to arrive as data — and this is the boundary where a licence fact becomes one. The
    operator determined on 2026-08-09 that EODHD-derived figures may be published (ADR
    0030, amended), so a shareable audience now receives the multiples.
    """

    async def test_it_carries_the_licence_note_verbatim(self, db_session, scene):
        output = await record_proposal(db_session, scene)
        await confirm(db_session, scene, output)

        table = await service.build(
            db_session,
            scene["job"],
            subject=subject_row(),
            peer_multiples={"PEER1": (result("ev_ebitda", "10"),)},
            basis=calc.MultipleBasis.TRAILING_TWELVE_MONTHS,
            as_of=AS_OF,
        )

        assert "the operator determined" in table.licence_note

    async def test_the_determination_is_read_from_the_policy(self, db_session, scene, monkeypatch):
        """**Read, not asserted**, and the patched policy is what proves the difference.

        Comparing the table's flag to the policy's passes trivially while both happen to
        say the same thing — a sabotage that hard-coded `True` here escaped exactly that
        assertion. Turning the policy off and watching the table follow is the only form
        of this test that can fail when the service stops consulting it.
        """
        monkeypatch.setitem(
            DEFAULT_POLICIES,
            Provider.EODHD,
            replace(DEFAULT_POLICIES[Provider.EODHD], derived_figures_publishable=False),
        )
        output = await record_proposal(db_session, scene)
        await confirm(db_session, scene, output)

        table = await service.build(
            db_session,
            scene["job"],
            subject=subject_row(),
            peer_multiples={"PEER1": (result("ev_ebitda", "10"),)},
            basis=calc.MultipleBasis.TRAILING_TWELVE_MONTHS,
            as_of=AS_OF,
        )

        assert table.derived_figures_publishable is False
        assert isinstance(table.for_audience(calc.Audience.SHAREABLE), calc.WithheldComps)

    async def test_it_follows_the_policy_when_the_policy_permits(
        self, db_session, scene, monkeypatch
    ):
        """The other direction, so the test above cannot pass by always returning false."""
        monkeypatch.setitem(
            DEFAULT_POLICIES,
            Provider.EODHD,
            replace(DEFAULT_POLICIES[Provider.EODHD], derived_figures_publishable=True),
        )
        output = await record_proposal(db_session, scene)
        await confirm(db_session, scene, output)

        table = await service.build(
            db_session,
            scene["job"],
            subject=subject_row(),
            peer_multiples={"PEER1": (result("ev_ebitda", "10"),)},
            basis=calc.MultipleBasis.TRAILING_TWELVE_MONTHS,
            as_of=AS_OF,
        )

        assert table.derived_figures_publishable is True
        assert table.for_audience(calc.Audience.SHAREABLE) is table

    async def test_a_shareable_audience_now_receives_the_multiples(self, db_session, scene):
        """The user-visible effect of the determination: the comps section of an exported
        report shows figures where it used to show a withholding paragraph."""
        output = await record_proposal(db_session, scene)
        await confirm(db_session, scene, output)
        table = await service.build(
            db_session,
            scene["job"],
            subject=subject_row(),
            peer_multiples={
                "PEER1": (result("ev_ebitda", "10"),),
                "PEER2": (result("ev_ebitda", "11"),),
            },
            basis=calc.MultipleBasis.TRAILING_TWELVE_MONTHS,
            as_of=AS_OF,
        )

        shared = table.for_audience(calc.Audience.SHAREABLE)

        assert shared is table
        assert len(shared.peers) == 2

    async def test_withdrawing_the_determination_closes_it_again(self, db_session, scene):
        """The gate survived the decision that opened it. A determination is about one
        agreement and can be revisited, so the machinery that enforces the closed state
        stays reachable and tested rather than being deleted as dead."""
        output = await record_proposal(db_session, scene)
        await confirm(db_session, scene, output)
        table = await service.build(
            db_session,
            scene["job"],
            subject=subject_row(),
            peer_multiples={"PEER1": (result("ev_ebitda", "10"),)},
            basis=calc.MultipleBasis.TRAILING_TWELVE_MONTHS,
            as_of=AS_OF,
        )

        closed = replace(table, derived_figures_publishable=False)
        withheld = closed.for_audience(calc.Audience.SHAREABLE)

        assert isinstance(withheld, calc.WithheldComps)
        assert withheld.peer_count == 1
        assert "withheld" in withheld.as_paragraph()


# -- Historical bands --------------------------------------------------------------------------


class TestTheSubjectAgainstItsOwnHistory:
    def test_the_band_reports_low_median_and_high(self, context):
        observations = [
            (date(2020, 6, 30), pure("10")),
            (date(2021, 6, 30), pure("12")),
            (date(2022, 6, 30), pure("14")),
            (date(2023, 6, 30), pure("16")),
            (date(2024, 6, 30), pure("18")),
        ]

        band = service.band_for(
            context,
            key="ev_ebitda",
            label="EV/EBITDA",
            basis=calc.MultipleBasis.TRAILING_TWELVE_MONTHS,
            observations=observations,
            current=pure("14"),
        )

        assert band.low == Decimal(10)
        assert band.median == Decimal(14)
        assert band.high == Decimal(18)
        assert band.count == 5

    def test_a_company_at_its_own_long_run_level_ranks_in_the_middle(self, context):
        """Often the more honest comparison: 14x against peers at 11x may just be this
        company."""
        observations = [(date(2020 + i, 6, 30), pure(str(10 + i * 2))) for i in range(5)]

        band = service.band_for(
            context,
            key="ev_ebitda",
            label="EV/EBITDA",
            basis=calc.MultipleBasis.TRAILING_TWELVE_MONTHS,
            observations=observations,
            current=pure("14"),
        )

        assert band.percentile == Decimal("0.6")

    def test_a_band_with_no_current_figure_has_no_percentile(self, context):
        observations = [(date(2020 + i, 6, 30), pure(str(10 + i))) for i in range(5)]

        band = service.band_for(
            context,
            key="ev_ebitda",
            label="EV/EBITDA",
            basis=calc.MultipleBasis.TRAILING_TWELVE_MONTHS,
            observations=observations,
        )

        assert band.percentile is None
        assert band.current is None

    def test_both_headline_figures_are_recorded_calculations(self, context):
        """So a band's median and percentile resolve to the ledger, not to a helper."""
        observations = [(date(2020 + i, 6, 30), pure(str(10 + i))) for i in range(5)]

        service.band_for(
            context,
            key="ev_ebitda",
            label="EV/EBITDA",
            basis=calc.MultipleBasis.TRAILING_TWELVE_MONTHS,
            observations=observations,
            current=pure("12"),
        )

        recorded = {record.name for record in context.records}
        assert {"median_multiple", "percentile_rank"} <= recorded


class TestTheSpecialistMultiples:
    def test_a_reit_is_pointed_at_p_ffo(self):
        assert service.specialist_multiples_for("reits") == ("p_ffo",)

    def test_a_bank_is_pointed_at_p_tbv(self):
        assert service.specialist_multiples_for("banks") == ("p_tbv",)

    def test_an_unclassified_company_gets_none(self):
        assert service.specialist_multiples_for("") == ()

    def test_a_sector_with_no_specialist_multiple_gets_none(self):
        assert service.specialist_multiples_for("utilities") == ()


class TestWhoConfirmedIt:
    async def test_the_approver_is_recoverable(self, db_session, scene):
        output = await record_proposal(db_session, scene)
        await confirm(db_session, scene, output)

        assert await service.confirmed_by(db_session, scene["job"]) == "analyst@example.invalid"

    async def test_an_unconfirmed_run_names_nobody(self, db_session, scene):
        await record_proposal(db_session, scene)
        assert await service.confirmed_by(db_session, scene["job"]) == ""


# -- The report says something, or nothing, and never a figure -------------------------------


class TestTheRenderedReportCarriesNoMultiple:
    """A Markdown report is the shareable artefact: it gets exported, attached and sent."""

    def test_the_renderer_cannot_be_handed_a_table(self):
        """The assembly takes a `WithheldComps`. There is no argument that carries figures.

        This is the ADR 0029 argument again: a rule enforced by a signature is one a later
        template cannot forget. Since task 46 the guarantee lives on `assemble_document` -
        the one walk every notation serialises - so a caller wanting the numbers in any
        rendered report has to change the assembler, which is a change somebody reviews.
        The serialisers downstream only ever see the already-written paragraph.
        """
        for entry in (assemble_document, render_markdown):
            annotation = inspect.signature(entry).parameters["comps"].annotation
            assert "WithheldComps" in str(annotation)
            assert "CompsTable" not in str(annotation)

    def test_a_run_with_no_peers_says_nothing(self):
        """ "No comps table" and "a comps table you are not shown" are different claims."""
        assert _comps_block(None) == []

    def test_a_run_with_peers_discloses_the_withholding(self):
        withheld = calc.WithheldComps(peer_count=3, excluded_count=1, as_of=AS_OF)
        block = _comps_block(withheld.as_paragraph())

        joined = "\n".join(block)
        assert "## Comparable companies" in joined
        assert "3 peer(s)" in joined
        assert "withheld" in joined

    def test_no_multiple_can_reach_the_block(self):
        """The type has no field that could carry one."""
        withheld = calc.WithheldComps(peer_count=3, excluded_count=1, as_of=AS_OF)

        assert not hasattr(withheld, "peers")
        assert not hasattr(withheld, "subject")
        assert not hasattr(withheld, "median_of")


class TestTheDeterministicProposal:
    async def test_a_company_with_no_sic_proposes_nobody(self, db_session, scene):
        subject = Company(name="Subject plc", cik="0000000001", ticker="SUBJ", exchange="NASDAQ")
        db_session.add(subject)
        await db_session.flush()

        assert (
            await service.propose_peers_from_sic(db_session, subject=subject, as_of=PERIOD_END)
            == ()
        )

    async def test_a_fresh_database_proposes_nobody(self, db_session, scene):
        """The honest answer for the first company anybody researches."""
        subject = Company(
            name="Subject plc", cik="0000000001", ticker="SUBJ", exchange="NASDAQ", sic="3571"
        )
        db_session.add(subject)
        await db_session.flush()

        assert (
            await service.propose_peers_from_sic(db_session, subject=subject, as_of=PERIOD_END)
            == ()
        )

    async def test_a_company_in_the_same_sic_group_is_proposed(self, db_session, scene):
        subject, peer = await seed_two_companies(
            db_session, scene, subject_sic="3571", peer_sic="3572"
        )

        proposals = await service.propose_peers_from_sic(
            db_session, subject=subject, as_of=PERIOD_END
        )

        assert [p.identifier for p in proposals] == [str(peer.id)]
        assert proposals[0].period_end == PERIOD_END
        assert "Shares SIC group 35" in proposals[0].rationale

    async def test_a_company_in_another_group_is_not(self, db_session, scene):
        subject, _ = await seed_two_companies(
            db_session, scene, subject_sic="3571", peer_sic="6021"
        )

        assert (
            await service.propose_peers_from_sic(db_session, subject=subject, as_of=PERIOD_END)
            == ()
        )

    async def test_a_candidate_with_no_facts_is_skipped(self, db_session, scene):
        """A peer with no period end cannot be aligned and would be excluded a step later."""
        subject, _ = await seed_two_companies(
            db_session, scene, subject_sic="3571", peer_sic="3572", peer_facts=False
        )

        assert (
            await service.propose_peers_from_sic(db_session, subject=subject, as_of=PERIOD_END)
            == ()
        )

    async def test_the_rationale_says_it_was_proposed_by_code(self, db_session, scene):
        """So a reviewer knows the judgement is theirs and has not already been made."""
        subject, _ = await seed_two_companies(
            db_session, scene, subject_sic="3571", peer_sic="3572"
        )

        proposals = await service.propose_peers_from_sic(
            db_session, subject=subject, as_of=PERIOD_END
        )

        assert "Proposed by code" in proposals[0].rationale
        assert "the judgement this gate asks for" in proposals[0].rationale


class TestTheProposalIsPointInTimeToo:
    async def test_a_peer_whose_only_facts_postdate_the_as_of_date_is_skipped(
        self, db_session, scene
    ):
        """The same clamp everything else obeys. A peer proposed on the strength of a filing
        nobody had yet is a peer chosen with hindsight."""
        subject, _ = await seed_two_companies(
            db_session, scene, subject_sic="3571", peer_sic="3572"
        )

        assert (
            await service.propose_peers_from_sic(
                db_session, subject=subject, as_of=PERIOD_END - timedelta(days=1)
            )
            == ()
        )
