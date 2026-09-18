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
import uuid
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select, text

from aer.agents.section_writer import SectionWriterAgent, SectionWriterInput
from aer.calc import comps as calc
from aer.calc.engine import CalculationContext
from aer.calc.units import DIMENSIONLESS, Quantity, SourceRef, Unit
from aer.config import HouseStyle
from aer.core.enums import Decision, GateKind, JobStatus, Provider, SourceTier, UserRole
from aer.core.hashing import canonical_json, sha256_hex
from aer.db.models import (
    Artefact,
    Calculation,
    Company,
    FinancialFact,
    JobStep,
    SourceDocument,
    User,
)
from aer.errors import ValidationError
from aer.fetch.policy import DEFAULT_POLICIES
from aer.render.document import CalculationFootnote, _comps_fragments, assemble_document
from aer.render.markdown import render_markdown, serialise_markdown
from aer.sections.evidence import SectionPolicy
from aer.sections.render import markdown_lines
from aer.sections.writing import _peer_set_for
from aer.services import approvals as approval_service
from aer.services import comps as comps_service
from aer.services import comps as service
from aer.services import comps_run
from aer.services.calculations import persist_context
from aer.workflow.workflows.vertical_slice_v1 import COMPS_STEP, comps_for, gate_payload
from tests.request_fixtures import research_request
from tests.workflow_fixtures import AS_OF_DATE, seed_job

pytestmark = pytest.mark.integration

_TABLES = "research_requests, audit_events, users, artefacts, prompts, companies"

PERIOD_END = date(2024, 6, 30)
AS_OF = date(2024, 6, 28)
SOURCE = SourceRef.security("test-fact")


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


STYLE = HouseStyle()


def _subject_table(
    values: dict[str, Decimal],
    *,
    publishable: bool,
    traced: bool = True,
    excluded: tuple[calc.PeerExclusion, ...] = (),
) -> calc.CompsTable:
    """A table holding only the subject's own multiples, each sourced as a run sources it.

    ``traced`` off is what a record written before the comps step stored the calculation
    id reads back as: the figure is real and the ledger holds its arithmetic, but the
    reference names the step rather than the row.
    """
    labels = {definition.key: definition.label for definition in calc.MULTIPLE_DEFINITIONS}
    step = SourceRef.calculation("comps:a-run", label="comps")
    return calc.CompsTable(
        subject=calc.PeerRow(
            identifier="SUBJ",
            name="Subject plc",
            period_end=PERIOD_END,
            multiples=tuple(
                calc.MultipleResult(
                    key=key,
                    label=labels[key],
                    quantity=Quantity.of(
                        value,
                        DIMENSIONLESS,
                        source=(
                            SourceRef.calculation(str(uuid.uuid4()), label=labels[key])
                            if traced
                            else step
                        ),
                    ),
                    basis=calc.MultipleBasis.LAST_FISCAL_YEAR,
                    period_end=PERIOD_END,
                )
                for key, value in values.items()
            ),
        ),
        peers=(),
        excluded=excluded,
        basis=calc.MultipleBasis.LAST_FISCAL_YEAR,
        as_of=AS_OF,
        peer_set_confirmed=True,
        derived_figures_publishable=publishable,
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

    request = research_request(
        user_id=analyst.id,
        company_name="Subject plc",
        ticker="SUBJ",
        exchange="NASDAQ",
        as_of_date=AS_OF_DATE,
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
            work_order_id=scene["request"].id,
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


# -- The report says something, or nothing, and a figure only where the licence allows -------


class TestTheRenderedReportCarriesWhatTheLicencePermits:
    """A report is the shareable artefact: it gets exported, attached and sent.

    ADR 0034 gave the assembler a parameter that could only be a `WithheldComps`, because
    ADR 0030 had read the terms and found no derived-data exemption. The operator's
    determination of 2026-08-09 changed the answer, and ADR 0034's amendment changed the
    signature to the union `for_audience` returns. What these tests hold is that the
    containment moved *upstream* rather than away.
    """

    def test_the_renderer_takes_what_for_audience_returns(self):
        """Both arms, and nothing wider: the union is the method's own return type."""
        produced = str(inspect.signature(calc.CompsTable.for_audience).return_annotation)
        for entry in (assemble_document, render_markdown):
            annotation = str(inspect.signature(entry).parameters["comps"].annotation)
            for arm in ("CompsTable", "WithheldComps"):
                assert arm in annotation
                assert arm in produced

    def test_a_run_with_no_comparison_says_nothing(self):
        """ "No comps table" and "a comps table you are not shown" are different claims."""
        assert _comps_fragments(None, [], style=HouseStyle()) == ()

    def test_the_withheld_arm_discloses_and_prints_no_figure(self):
        citations: list = []
        withheld = calc.WithheldComps(peer_count=3, excluded_count=1, as_of=AS_OF)

        joined = "\n".join(markdown_lines(_comps_fragments(withheld, citations, style=STYLE)))

        assert "## Comparable companies" in joined
        assert "three peers" in joined
        assert "withheld" in joined
        # No table, and nothing cited: there is no figure in the object to cite.
        assert "|" not in joined
        assert citations == []

    def test_no_multiple_can_reach_the_withheld_arm(self):
        """The type has no field that could carry one."""
        withheld = calc.WithheldComps(peer_count=3, excluded_count=1, as_of=AS_OF)

        assert not hasattr(withheld, "peers")
        assert not hasattr(withheld, "subject")
        assert not hasattr(withheld, "median_of")

    def test_the_table_arm_prints_the_subject_s_own_multiples(self):
        citations: list = []
        table = _subject_table(
            {"pe": Decimal("27.431"), "ev_ebitda": Decimal("18.925")}, publishable=True
        )

        joined = "\n".join(
            markdown_lines(
                _comps_fragments(
                    table.for_audience(calc.Audience.SHAREABLE), citations, style=STYLE
                )
            )
        )

        assert "| P/E | 27.43\N{MULTIPLICATION SIGN}[^1] |" in joined
        assert "| EV/EBITDA | 18.93\N{MULTIPLICATION SIGN}[^2] |" in joined
        assert [ref.kind for ref in citations] == ["calculation", "calculation"]

    def test_withdrawing_the_determination_withdraws_the_figures(self):
        """The same table, the same renderer, the flag off: a disclosure and no number."""
        citations: list = []
        table = _subject_table({"pe": Decimal("27.431")}, publishable=False)

        joined = "\n".join(
            markdown_lines(
                _comps_fragments(
                    table.for_audience(calc.Audience.SHAREABLE), citations, style=STYLE
                )
            )
        )

        assert isinstance(table.for_audience(calc.Audience.SHAREABLE), calc.WithheldComps)
        assert "27.43" not in joined
        assert "no peer survived to be compared" in joined
        assert citations == []

    def test_the_confirmed_peers_are_named_with_their_reasons(self):
        """ADR 0034's amendment of 2026-09-18. A judge's words on the report this fixes:
        "they are not even listed, so there is no relative anchor of any kind"."""
        citations: list = []
        table = _subject_table(
            {"pe": Decimal("27.431")},
            publishable=True,
            excluded=(
                calc.PeerExclusion(
                    identifier="P1",
                    name="Oracle Corp",
                    reason="no price series",
                    rationale="Competes directly in enterprise cloud.",
                ),
                calc.PeerExclusion(
                    identifier="P2",
                    name="SAP SE",
                    reason="no price series",
                    rationale="Overlapping enterprise application suite.",
                ),
            ),
        )

        joined = "\n".join(
            markdown_lines(
                _comps_fragments(
                    table.for_audience(calc.Audience.SHAREABLE), citations, style=STYLE
                )
            )
        )

        assert "Oracle Corp" in joined
        assert "Competes directly in enterprise cloud." in joined
        assert "SAP SE" in joined
        # The names are the operator's judgement, not a filed fact: no marker is taken
        # for them, so the only citations are the subject's own multiple (ADR 0074).
        assert [ref.kind for ref in citations] == ["calculation"]
        assert "[^2]" not in joined

    def test_the_names_cross_even_when_the_figures_do_not(self):
        """The licence is about the vendor's prices. The set is the operator's own work,
        and withdrawing the determination must not cost a reader the competitive anchor."""
        table = _subject_table(
            {"pe": Decimal("27.431")},
            publishable=False,
            excluded=(
                calc.PeerExclusion(
                    identifier="P1",
                    name="Oracle Corp",
                    reason="no price series",
                    rationale="Rival.",
                ),
            ),
        )
        withheld = table.for_audience(calc.Audience.SHAREABLE)

        joined = "\n".join(markdown_lines(_comps_fragments(withheld, [], style=STYLE)))

        assert isinstance(withheld, calc.WithheldComps)
        assert "27.43" not in joined
        assert "Oracle Corp" in joined

    def test_a_run_whose_peers_are_all_unnamed_prints_no_list(self):
        joined = "\n".join(
            markdown_lines(
                _comps_fragments(
                    calc.WithheldComps(peer_count=0, excluded_count=2, as_of=AS_OF),
                    [],
                    style=STYLE,
                )
            )
        )

        assert "comparable set for this research" not in joined

    def test_a_multiple_with_no_calculation_behind_it_is_not_printed(self):
        """A figure prints only with a marker that resolves — ADR 0034's amendment.

        A record written before the comps step stored each multiple's calculation id
        sources its figures to the step itself. Citing the step would resolve to nothing
        and print the report's own broken-citation notice against a sound figure, so such
        a record renders the way it always did: the disclosure, and no table.
        """
        citations: list = []
        table = _subject_table({"pe": Decimal("27.431")}, publishable=True, traced=False)

        joined = "\n".join(
            markdown_lines(
                _comps_fragments(
                    table.for_audience(calc.Audience.SHAREABLE), citations, style=STYLE
                )
            )
        )

        assert "27.43" not in joined
        assert "|" not in joined
        assert citations == []


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


class TestTheNoteReportsWhatTheStepBuilt:
    """Gap A53: the disclosure's counts are the comps step's own, never a re-alignment.

    The first live run's table held no peer — none could be priced — while the render-time
    note re-aligned the confirmed set by date, counted one survivor, and printed
    "performed against 1 peer(s)" over an analysis that exists in no version anywhere.
    """

    @staticmethod
    def _outcome(*, peers: int, excluded: int) -> dict[str, Any]:
        """What the step writes for a table of that shape — its own serialiser, not a guess.

        Hand-written fixtures here used to carry the two counts and none of the rows they
        are counts of, which is a shape `CompsOutcome.as_dict` cannot produce. The report
        reads the rows now, so an unfaithful fixture would have hidden exactly the thing
        this class exists to pin.
        """
        table = calc.CompsTable(
            subject=subject_row(),
            peers=tuple(
                calc.PeerRow(
                    identifier=f"PEER{index + 1}",
                    name=f"Peer {index + 1} plc",
                    period_end=PERIOD_END,
                    multiples=(result("ev_ebitda", "11"),),
                )
                for index in range(peers)
            ),
            excluded=tuple(
                calc.PeerExclusion(
                    identifier=f"OUT{index + 1}",
                    name=f"Left Out {index + 1} plc",
                    reason="this research holds no price series for it",
                    period_end=PERIOD_END,
                )
                for index in range(excluded)
            ),
            basis=calc.MultipleBasis.TRAILING_TWELVE_MONTHS,
            as_of=AS_OF,
            peer_set_confirmed=True,
        )
        return comps_run.CompsOutcome(built=True, table=table).as_dict()

    @staticmethod
    async def _record_comps_outcome(
        session: Any, scene: dict[str, Any], output: dict[str, Any]
    ) -> None:
        """Write a comps step row the way the workflow writes one."""
        session.add(
            JobStep(
                job_id=scene["job"].id,
                step_key=COMPS_STEP,
                sequence=9,
                status=JobStatus.SUCCEEDED,
                idempotency_key=f"{scene['job'].id}:{COMPS_STEP}",
                input_hash="0" * 64,
                output_ref=output,
            )
        )
        await session.flush()

    async def _confirmed(self, db_session: Any, scene: dict[str, Any]) -> None:
        await confirm(db_session, scene, await record_proposal(db_session, scene))

    async def test_the_counts_are_the_steps_not_a_realignment(self, db_session, scene):
        """Both proposed peers share the subject's period end, so a date re-alignment
        would count two; the step's record says one made the table."""
        await self._confirmed(db_session, scene)
        await self._record_comps_outcome(db_session, scene, self._outcome(peers=1, excluded=1))

        note = await comps_for(db_session, job=scene["job"], request=scene["request"])

        assert note is not None
        assert (note.peer_count, note.excluded_count) == (1, 1)
        assert note.as_of == AS_OF

    async def test_an_empty_table_discloses_that_nothing_was_computed(self, db_session, scene):
        await self._confirmed(db_session, scene)
        await self._record_comps_outcome(db_session, scene, self._outcome(peers=0, excluded=2))

        note = await comps_for(db_session, job=scene["job"], request=scene["request"])

        assert note is not None
        assert note.peer_count == 0
        paragraph = note.as_paragraph()
        # Case-insensitively, because where the phrase sits in the sentence is the
        # disclosure's business rather than this test's. It moved to a sentence opening when
        # the note started naming *why* each peer was excluded (roadmap §4.15), and the
        # capital letter that came with it broke an assertion nobody could see fail: this
        # test needs a database, so it skips on a machine with nothing started.
        assert "no comparable figure was computed" in paragraph.lower()
        assert "available in full" not in paragraph

    async def test_a_confirmed_set_whose_comps_step_has_not_run_yields_no_note(
        self, db_session, scene
    ):
        """A preview before the step, or a run recorded before the step existed: no
        comparison was performed, and silence is that claim."""
        await self._confirmed(db_session, scene)

        assert await comps_for(db_session, job=scene["job"], request=scene["request"]) is None

    async def test_a_step_that_built_no_table_yields_no_note(self, db_session, scene):
        await self._confirmed(db_session, scene)
        await self._record_comps_outcome(
            db_session, scene, {"comps": False, "reason": "no annual period"}
        )

        assert await comps_for(db_session, job=scene["job"], request=scene["request"]) is None

    async def test_an_outcome_carrying_no_exclusions_claims_none(self, db_session, scene):
        """A step output old enough to record no exclusions says less, never more.

        This used to infer the count from the confirmed set — two confirmed, one in the
        table, so one excluded — which is `build`'s invariant and also the kind of
        render-time inference this class exists to forbid. The report now reads the rows
        the step recorded, and a record holding none makes no claim about exclusions
        rather than a correct guess at them.
        """
        await self._confirmed(db_session, scene)
        await self._record_comps_outcome(
            db_session, scene, {"comps": True, "peers": 1, "as_of": AS_OF.isoformat()}
        )

        note = await comps_for(db_session, job=scene["job"], request=scene["request"])

        assert note is not None
        assert note.excluded_count == 0
        assert "excluded" not in note.as_paragraph()


class TestTheSubjectsOwnMultiplesReachTheDocument:
    """Phase 4.3, end to end: what the step recorded, through the licence gate, into a
    rendered report whose marker resolves to the arithmetic.

    Every link was already built and one was not connected. The multiple was computed and
    traced; the record carried the figure and not the calculation behind it; the assembler
    would not accept a table; and ADR 0030's amendment had said six weeks earlier that the
    figures should be in the document. This is the chain, asserted whole, because each
    piece of it passed its own tests while the report said the figures were withheld.
    """

    @staticmethod
    async def _run_the_comps_step(session: Any, scene: dict[str, Any]) -> tuple[str, Decimal]:
        """Strike a real traced multiple, persist its ledger row, record the step output."""
        context = CalculationContext(code_version="4point3test")
        price = Quantity.of(Decimal("512.34"), Unit.currency("USD") / Unit.base("shares"))
        earnings = Quantity.of(Decimal("18.55"), Unit.currency("USD") / Unit.base("shares"))
        computed = calc.multiples_for(
            context,
            inputs={
                "price_per_share": price.with_source(SourceRef.security("price-fact")),
                "earnings_per_share": earnings.with_source(SourceRef.security("eps-fact")),
            },
            basis=calc.MultipleBasis.LAST_FISCAL_YEAR,
            period_end=PERIOD_END,
        )
        await persist_context(session, context, job_id=scene["job"].id)

        table = calc.CompsTable(
            subject=calc.PeerRow(
                identifier="SUBJ", name="Subject plc", period_end=PERIOD_END, multiples=computed
            ),
            peers=(),
            excluded=(
                calc.PeerExclusion(
                    identifier="PEER1",
                    name="Peer One plc",
                    reason=comps_service.UNACQUIRED_PEER_REASON,
                    period_end=PERIOD_END,
                ),
            ),
            basis=calc.MultipleBasis.LAST_FISCAL_YEAR,
            as_of=AS_OF,
            peer_set_confirmed=True,
            licence_note=DEFAULT_POLICIES[Provider.EODHD].licence_note,
            derived_figures_publishable=True,
        )
        record = comps_run.CompsOutcome(built=True, table=table).as_dict()
        session.add(
            JobStep(
                job_id=scene["job"].id,
                step_key=COMPS_STEP,
                sequence=9,
                status=JobStatus.SUCCEEDED,
                idempotency_key=f"{scene['job'].id}:{COMPS_STEP}",
                input_hash="0" * 64,
                output_ref=record,
            )
        )
        await session.flush()

        row = next(item for item in record["subject_multiples"] if item["key"] == "pe")
        return str(row["calculation"]), Decimal(str(row["value"]))

    async def test_the_step_records_which_calculation_each_multiple_came_from(
        self, db_session, scene
    ):
        """`@traced` puts the id on the quantity; recording it is one field."""
        await confirm(db_session, scene, await record_proposal(db_session, scene))
        identifier, value = await self._run_the_comps_step(db_session, scene)

        stored = await db_session.get(Calculation, uuid.UUID(identifier))

        assert stored is not None
        # The step's record keeps the quotient at the calculation context's precision and
        # the ledger column keeps twelve places, so they are the same figure rounded
        # differently. Both round to the same thing at the two decimals a report prints.
        assert stored.output_value == value.quantize(stored.output_value)
        assert stored.formula == "multiple = numerator / denominator"

    async def test_the_rendered_report_prints_the_multiple_and_resolves_its_marker(
        self, db_session, scene
    ):
        await confirm(db_session, scene, await record_proposal(db_session, scene))
        identifier, _ = await self._run_the_comps_step(db_session, scene)

        comps = await comps_for(db_session, job=scene["job"], request=scene["request"])
        document = await assemble_document(
            db_session, job=scene["job"], request=scene["request"], comps=comps
        )
        rendered = serialise_markdown(document)

        assert isinstance(comps, calc.CompsTable)
        assert "| P/E | 27.62\N{MULTIPLICATION SIGN}[^1] |" in rendered
        # Named, so a reader knows whose figures a one-column table holds without the
        # paragraph promising them.
        assert "| Multiple | Subject plc |" in rendered
        # And the marker resolves to the arithmetic rather than to a broken-citation note.
        footnote = document.footnotes[0]
        assert isinstance(footnote, CalculationFootnote)
        assert footnote.formula == "multiple = numerator / denominator"
        assert document.citations[0].identifier == identifier

    async def test_a_record_written_before_the_id_was_stored_shows_no_figure(
        self, db_session, scene
    ):
        """The corpus's five stored runs. The figures are real and cannot be footnoted.

        Said rather than passed over: a section that simply stopped would read as an
        analysis nobody did.
        """
        await confirm(db_session, scene, await record_proposal(db_session, scene))
        identifier, _ = await self._run_the_comps_step(db_session, scene)

        step = await db_session.scalar(
            select(JobStep).where(JobStep.job_id == scene["job"].id, JobStep.step_key == COMPS_STEP)
        )
        step.output_ref = {
            **step.output_ref,
            "subject_multiples": [
                {**row, "calculation": None} for row in step.output_ref["subject_multiples"]
            ],
        }
        await db_session.flush()

        comps = await comps_for(db_session, job=scene["job"], request=scene["request"])
        document = await assemble_document(
            db_session, job=scene["job"], request=scene["request"], comps=comps
        )
        rendered = serialise_markdown(document)

        assert identifier not in rendered
        assert "27.62" not in rendered
        assert "does not say which calculation produced each of them" in rendered
        assert document.citations == []


class TestTheOperatorMayAddAComparable:
    """The peer set arrived as a model's proposal or the deterministic floor, and a person
    could only take it or leave it. The first acceptance pass asked for the third option.

    **A company this platform already holds, not a ticker to go and resolve.** The web
    process has no source client and should not have one; the floor draws from exactly
    this pool, and a company with no stored facts could not be aligned against the subject
    in any case.
    """

    async def test_an_addition_joins_the_set_the_gate_hashes(
        self, db_session: Any, scene: dict[str, Any]
    ) -> None:
        await record_proposal(db_session, scene)
        _, peer = await seed_two_companies(db_session, scene, subject_sic="3571", peer_sic="3571")

        await comps_service.add_operator_peer(
            db_session,
            job=scene["job"],
            company_id=peer.id,
            rationale="Same industry, and the platform already holds its filings.",
            actor=scene["analyst"],
        )

        payload = await comps_service.payload_for_job(db_session, scene["job"].id)
        identifiers = [row["identifier"] for row in payload["peers"]]
        assert str(peer.id) in identifiers
        added = next(row for row in payload["peers"] if row["identifier"] == str(peer.id))
        # The registry's name, not anything typed.
        assert added["name"] == "Peer plc"
        assert added["period_end"] == PERIOD_END.isoformat()

    async def test_the_gate_hashes_the_set_the_page_shows_and_the_approval_releases_it(
        self, db_session: Any, scene: dict[str, Any]
    ) -> None:
        """Readiness audit 2026-09, blocking: the page hashed the whole set, the engine
        hashed the step's proposal alone, so an approval taken from the page after an
        addition never matched and the run could not leave the gate."""
        await record_proposal(db_session, scene)
        _, peer = await seed_two_companies(db_session, scene, subject_sic="3571", peer_sic="3571")
        await comps_service.add_operator_peer(
            db_session,
            job=scene["job"],
            company_id=peer.id,
            rationale="Same industry, and the platform already holds its filings.",
            actor=scene["analyst"],
        )
        shown = await comps_service.payload_for_job(db_session, scene["job"].id)

        # One funnel: what the run hashes for the gate is what the page renders.
        assert (
            await gate_payload(db_session, job=scene["job"], gate=GateKind.PEER_SET.value) == shown
        )

        await approve_plan(db_session, scene)
        await approval_service.record_decision(
            db_session,
            job=scene["job"],
            gate=GateKind.PEER_SET,
            decision=Decision.APPROVED,
            actor=scene["analyst"],
            payload_hash=sha256_hex(canonical_json(shown)),
        )
        confirmed = await comps_service.confirmed_peer_set(db_session, scene["job"])
        assert str(peer.id) in {row.identifier for row in confirmed}

    async def test_a_company_with_no_stored_facts_yields_nothing(
        self, db_session: Any, scene: dict[str, Any]
    ) -> None:
        """The rule the floor already obeys: a peer with no period end cannot be aligned
        against the subject and would be excluded a step later anyway."""
        await record_proposal(db_session, scene)
        _, peer = await seed_two_companies(
            db_session, scene, subject_sic="3571", peer_sic="3571", peer_facts=False
        )

        await comps_service.add_operator_peer(
            db_session,
            job=scene["job"],
            company_id=peer.id,
            rationale="Comparable on the face of it.",
            actor=scene["analyst"],
        )

        payload = await comps_service.payload_for_job(db_session, scene["job"].id)
        assert str(peer.id) not in [row["identifier"] for row in payload["peers"]]

    async def test_a_rationale_is_required(self, db_session: Any, scene: dict[str, Any]) -> None:
        """A badly chosen peer moves a median more than most modelling choices do and does
        it invisibly, which is what this gate exists to catch."""
        await record_proposal(db_session, scene)
        _, peer = await seed_two_companies(db_session, scene, subject_sic="3571", peer_sic="3571")

        with pytest.raises(ValidationError, match="needs a reason"):
            await comps_service.add_operator_peer(
                db_session,
                job=scene["job"],
                company_id=peer.id,
                rationale="  ",
                actor=scene["analyst"],
            )

    async def test_a_company_the_registry_does_not_hold_is_refused(
        self, db_session: Any, scene: dict[str, Any]
    ) -> None:
        await record_proposal(db_session, scene)

        with pytest.raises(ValidationError, match="No company"):
            await comps_service.add_operator_peer(
                db_session,
                job=scene["job"],
                company_id=uuid.uuid4(),
                rationale="A company that does not exist here.",
                actor=scene["analyst"],
            )

    async def test_one_already_on_the_set_is_refused(
        self, db_session: Any, scene: dict[str, Any]
    ) -> None:
        _, peer = await seed_two_companies(db_session, scene, subject_sic="3571", peer_sic="3571")
        await record_proposal(
            db_session,
            scene,
            peers=[
                {
                    "identifier": str(peer.id),
                    "name": "Peer plc",
                    "rationale": "Proposed by the floor.",
                    "period_end": PERIOD_END.isoformat(),
                }
            ],
        )

        with pytest.raises(ValidationError, match="already carries"):
            await comps_service.add_operator_peer(
                db_session,
                job=scene["job"],
                company_id=peer.id,
                rationale="A second reason for the same company.",
                actor=scene["analyst"],
            )

    async def test_the_picker_offers_only_what_the_table_could_use(
        self, db_session: Any, scene: dict[str, Any]
    ) -> None:
        """Held, with facts, and not already on the set — offering anything else would be
        offering the operator a refusal."""
        await record_proposal(db_session, scene, peers=[])
        _, peer = await seed_two_companies(db_session, scene, subject_sic="3571", peer_sic="3571")

        offered = await comps_service.addable_companies(db_session, job_id=scene["job"].id)
        names = [name for _, name in offered]
        assert "Peer plc" in names
        # The subject has no facts of its own in this fixture, so it is not offered either
        # — but once added, a peer stops being offered.
        await comps_service.add_operator_peer(
            db_session,
            job=scene["job"],
            company_id=peer.id,
            rationale="Same industry.",
            actor=scene["analyst"],
        )
        again = await comps_service.addable_companies(db_session, job_id=scene["job"].id)
        assert "Peer plc" not in [name for _, name in again]

    async def test_adding_after_approving_invalidates_the_approval(
        self, db_session: Any, scene: dict[str, Any]
    ) -> None:
        """The stale-approval rule working: the operator approved a set, and this is no
        longer that set."""
        output = await record_proposal(db_session, scene)
        await confirm(db_session, scene, output)
        assert await comps_service.confirmed_peer_set(db_session, scene["job"])

        _, peer = await seed_two_companies(db_session, scene, subject_sic="3571", peer_sic="3571")
        await comps_service.add_operator_peer(
            db_session,
            job=scene["job"],
            company_id=peer.id,
            rationale="Same industry.",
            actor=scene["analyst"],
        )

        with pytest.raises(comps_service.PeerSetNotConfirmedError, match="different set"):
            await comps_service.confirmed_peer_set(db_session, scene["job"])


class TestTheSectionThatNamesCompetitorsIsToldWhoTheyAre:
    """Migration 0080, and ADR 0034's amendment of 2026-09-18 on the writer's side.

    A live report discussed competition for four hundred words and named nobody, while the
    run held eight confirmed comparables with written rationales. They are not facts,
    calculations or excerpts, so the evidence pack has never had anywhere to put them; they
    reach the writer as context beside it, with no id and nothing to cite.
    """

    @staticmethod
    def _policy(*, names_peers: bool) -> Any:
        return SectionPolicy(
            min_sources=0,
            requires_primary=False,
            max_tier_rank=5,
            allow_forward_looking=False,
            token_budget=1_000,
            names_peers=names_peers,
        )

    async def test_a_confirmed_set_reaches_the_section_that_asked(
        self, db_session: Any, scene: dict[str, Any]
    ) -> None:
        output = await record_proposal(db_session, scene)
        await confirm(db_session, scene, output)

        lines = await _peer_set_for(
            db_session, job_id=scene["job"].id, policy=self._policy(names_peers=True)
        )

        assert lines == (
            "Peer One plc — Same industry, similar revenue",
            "Peer Two plc — Same end market",
        )

    async def test_every_other_section_is_told_nothing(
        self, db_session: Any, scene: dict[str, Any]
    ) -> None:
        output = await record_proposal(db_session, scene)
        await confirm(db_session, scene, output)

        assert (
            await _peer_set_for(
                db_session, job_id=scene["job"].id, policy=self._policy(names_peers=False)
            )
            == ()
        )

    async def test_an_unconfirmed_set_costs_the_names_and_not_the_section(
        self, db_session: Any, scene: dict[str, Any]
    ) -> None:
        """`confirmed_peer_set` refuses rather than returning nothing, because an
        unconfirmed set reaching a *comps table* would be a comparison nobody agreed to. A
        section that cannot name its competitors is thinner, not wrong, and failing the
        drafting step over it would trade a real defect for a worse one."""
        await record_proposal(db_session, scene)

        with pytest.raises(comps_service.PeerSetNotConfirmedError):
            await comps_service.confirmed_peer_set(db_session, scene["job"])
        assert (
            await _peer_set_for(
                db_session, job_id=scene["job"].id, policy=self._policy(names_peers=True)
            )
            == ()
        )

    def test_the_prompt_states_the_boundary_the_numeral_rule_cannot(self) -> None:
        """A figure attributed to a peer would be refused for having no lineage. Nothing
        refuses an unsupported *qualitative* claim about one, so the limit is said."""
        composed = SectionWriterAgent().user_message(
            SectionWriterInput(
                section_key="industry_landscape",
                title="Industry & Competitive Positioning",
                company_name="Subject plc",
                ticker="SUBJ",
                as_of_date=AS_OF.isoformat(),
                output_contract={},
                peer_set=["Oracle Corp — Competes in enterprise cloud."],
            )
        )

        assert "Oracle Corp — Competes in enterprise cloud." in composed
        assert "no filings, figures or statements for any of them" in composed

    def test_a_section_with_no_peers_gets_no_block(self) -> None:
        composed = SectionWriterAgent().user_message(
            SectionWriterInput(
                section_key="balance_sheet",
                title="Balance Sheet",
                company_name="Subject plc",
                ticker="SUBJ",
                as_of_date=AS_OF.isoformat(),
                output_contract={},
            )
        )

        assert "confirmed these companies as comparable" not in composed
