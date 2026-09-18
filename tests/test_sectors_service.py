"""Classification against the database: proposed, confirmed, and only then acted on.

The enforcement tests in `test_sectors_enforcement.py` assert that a mandate for a blocked
model cannot exist. These assert the other half, which is where the real hole would be: that
a specialist classification nobody confirmed **stops the run** rather than falling through to
"unclassified".

Falling through is the tempting implementation and the wrong one. It fails safe in the
direction that looks safe — no error, no gate, the standard model runs — and unsafe in the
direction that matters, because "unclassified" is the permissive state and a run reaches it
by *forgetting* rather than by deciding.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
from sqlalchemy import text

from aer.core.enums import Decision, GateKind, JobStatus, UserRole
from aer.core.hashing import canonical_json, sha256_hex
from aer.core.sectors import (
    SECTOR_PROFILES,
    ModelNotPermittedError,
    SicScheme,
    ValuationModel,
    profile_for,
)
from aer.db.models import Company, JobStep, SectionStatus, User
from aer.render.markdown import SectorNote, _sector_block, render_markdown
from aer.sections.registry import (
    create_report_sections,
    resolve_sections,
    sections_for_job,
)
from aer.services import approvals as approval_service
from aer.services import sectors as sector_service
from aer.services.sectors import (
    CLASSIFY_STEP,
    classification_payload,
    confirmed_classification,
    mandate_for_job,
    metric_disclosure,
    propose_from_sic,
    sector_gate_required,
)
from aer.workflow.engine import StepContext
from aer.workflow.workflows.vertical_slice_v1 import _classify
from tests.request_fixtures import research_request
from tests.workflow_fixtures import AS_OF_DATE, seed_job

pytestmark = pytest.mark.integration

_TABLES = "research_requests, audit_events, users, artefacts, prompts, companies"


@pytest.fixture
async def scene(db_session: Any) -> dict[str, Any]:
    await db_session.execute(text(f"TRUNCATE {_TABLES} RESTART IDENTITY CASCADE"))

    analyst = User(email="analyst@example.invalid", display_name="Analyst", role=UserRole.ANALYST)
    db_session.add(analyst)
    await db_session.flush()

    request = research_request(
        user_id=analyst.id,
        company_name="Barclays PLC",
        ticker="BARC",
        exchange="LSE",
        as_of_date=AS_OF_DATE,
        base_currency="GBP",
        reporting_currency="GBP",
        investment_horizon_months=12,
        max_cost_gbp="2.50",
    )
    db_session.add(request)
    await db_session.flush()

    job = await seed_job(db_session, request=request)
    return {"request": request, "job": job, "analyst": analyst}


async def record_classification(session: Any, scene: dict[str, Any], sector_key: str) -> dict:
    """Write a classify step the way the workflow writes one."""
    profile = profile_for(sector_key)
    output = {
        "sector_key": sector_key,
        "sector_label": profile.label if profile else "",
        "rationale": f"SIC lookup proposed {sector_key or 'nothing specialist'}.",
        "proposed_by": "sic_lookup",
        "allowed_models": [m.value for m in profile.allowed_models] if profile else [],
        "blocked_models": [m.value for m in profile.blocked_models] if profile else [],
        "warnings": list(profile.warnings) if profile else [],
    }
    output["payload_hash"] = sha256_hex(canonical_json(classification_payload(output)))

    session.add(
        JobStep(
            job_id=scene["job"].id,
            step_key=CLASSIFY_STEP,
            sequence=3,
            status=JobStatus.SUCCEEDED,
            idempotency_key=f"{scene['job'].id}:{CLASSIFY_STEP}",
            input_hash="0" * 64,
            output_ref=output,
        )
    )
    await session.flush()
    return output


async def approve_plan(session: Any, scene: dict[str, Any]) -> None:
    """Gates are passed in order, so the sector gate needs the plan gate behind it."""
    await approval_service.record_decision(
        session,
        job=scene["job"],
        gate=GateKind.PLAN,
        decision=Decision.APPROVED,
        actor=scene["analyst"],
        payload_hash="1" * 64,
    )


async def confirm(session: Any, scene: dict[str, Any], payload_hash: str) -> None:
    await approve_plan(session, scene)
    await approval_service.record_decision(
        session,
        job=scene["job"],
        gate=GateKind.SECTOR_SPECIALIST,
        decision=Decision.APPROVED,
        actor=scene["analyst"],
        payload_hash=payload_hash,
    )


# -- The hole this closes --------------------------------------------------------------------


class TestAnUnconfirmedClassificationStopsTheRun:
    async def test_a_proposed_bank_with_no_approval_refuses(self, db_session, scene):
        await record_classification(db_session, scene, "banks")

        with pytest.raises(ModelNotPermittedError, match="nobody has confirmed it"):
            await confirmed_classification(db_session, scene["job"])

    async def test_it_does_not_fall_through_to_unclassified(self, db_session, scene):
        """The failure this test exists for.

        Returning `(None, "")` here would be an error nobody sees: the run would continue,
        the standard model would be permitted, and a discounted cash flow on a bank would
        reach the report with nothing anywhere saying a classification had been dropped.
        """
        await record_classification(db_session, scene, "banks")

        with pytest.raises(ModelNotPermittedError):
            await mandate_for_job(
                db_session, scene["job"], model=ValuationModel.DCF_FCFF, subject="BARC"
            )

    async def test_a_rejected_gate_does_not_count_as_a_confirmation(self, db_session, scene):
        output = await record_classification(db_session, scene, "banks")
        await approve_plan(db_session, scene)
        await approval_service.record_decision(
            db_session,
            job=scene["job"],
            gate=GateKind.SECTOR_SPECIALIST,
            decision=Decision.REJECTED,
            actor=scene["analyst"],
            payload_hash=str(output["payload_hash"]),
            notes="Not a bank; it is the holding company.",
        )

        with pytest.raises(ModelNotPermittedError, match="nobody has confirmed it"):
            await confirmed_classification(db_session, scene["job"])

    async def test_an_approval_of_a_different_proposal_is_not_an_approval_of_this(
        self, db_session, scene
    ):
        """The same rule the plan gate uses. An approval is of a payload, not of a gate."""
        await record_classification(db_session, scene, "banks")
        await confirm(db_session, scene, "0" * 64)

        with pytest.raises(ModelNotPermittedError, match="does not match the one on record"):
            await confirmed_classification(db_session, scene["job"])


# -- Confirmed, and then enforced ------------------------------------------------------------


class TestAConfirmedClassificationEnforces:
    async def test_a_confirmed_bank_refuses_a_discounted_cash_flow(self, db_session, scene):
        output = await record_classification(db_session, scene, "banks")
        await confirm(db_session, scene, str(output["payload_hash"]))

        with pytest.raises(ModelNotPermittedError, match="blocked for banks"):
            await mandate_for_job(
                db_session, scene["job"], model=ValuationModel.DCF_FCFF, subject="BARC"
            )

    async def test_the_same_bank_gets_a_comparables_mandate(self, db_session, scene):
        output = await record_classification(db_session, scene, "banks")
        await confirm(db_session, scene, str(output["payload_hash"]))

        mandate = await mandate_for_job(
            db_session, scene["job"], model=ValuationModel.COMPS_MULTIPLES, subject="BARC"
        )

        assert mandate.sector_key == "banks"
        assert mandate.confirmed_by == "analyst@example.invalid"

    async def test_the_confirmer_reaches_the_mandate(self, db_session, scene):
        """Who agreed is part of the record, not a log line."""
        output = await record_classification(db_session, scene, "reits")
        await confirm(db_session, scene, str(output["payload_hash"]))

        mandate = await mandate_for_job(
            db_session, scene["job"], model=ValuationModel.NET_ASSET_VALUE, subject="SPG"
        )

        assert mandate.confirmed_by == "analyst@example.invalid"

    async def test_the_warnings_travel_with_the_mandate(self, db_session, scene):
        output = await record_classification(db_session, scene, "reits")
        await confirm(db_session, scene, str(output["payload_hash"]))

        mandate = await mandate_for_job(
            db_session, scene["job"], model=ValuationModel.COMPS_MULTIPLES, subject="SPG"
        )

        assert any("Depreciation dominates" in w for w in mandate.warnings)


# -- The ordinary run ------------------------------------------------------------------------


class TestAnOrdinaryCompanyNeedsNoGate:
    async def test_nothing_specialist_means_no_confirmation_required(self, db_session, scene):
        await record_classification(db_session, scene, "")

        profile, confirmed_by = await confirmed_classification(db_session, scene["job"])

        assert profile is None
        assert confirmed_by == ""

    async def test_it_gets_a_standard_model_mandate(self, db_session, scene):
        await record_classification(db_session, scene, "")

        mandate = await mandate_for_job(
            db_session, scene["job"], model=ValuationModel.DCF_FCFF, subject="MSFT"
        )

        assert mandate.sector_key == ""
        assert mandate.warnings == ()

    async def test_a_run_that_has_not_classified_yet_is_treated_as_ordinary(
        self, db_session, scene
    ):
        """No classify step at all. The gate machinery has to cope with an unfinished run."""
        profile, _ = await confirmed_classification(db_session, scene["job"])
        assert profile is None


# -- The gate's own condition ----------------------------------------------------------------


class TestTheGateFiresOnlyWhereItMatters:
    @pytest.mark.parametrize(
        ("sector", "required"),
        [("banks", True), ("reits", True), ("utilities", True), ("", False)],
    )
    def test_a_specialist_proposal_opens_it(self, sector, required):
        assert sector_gate_required({"sector_key": sector}) is required

    def test_an_unknown_sector_does_not_open_it(self):
        """A key with no profile enforces nothing, so a gate on it would ask about nothing."""
        assert sector_gate_required({"sector_key": "crypto_miners"}) is False

    def test_the_payload_is_what_the_operator_sees(self):
        payload = classification_payload(
            {
                "sector_key": "banks",
                "rationale": "SIC 6021.",
                "proposed_by": "sic_lookup",
                "blocked_models": ["dcf_fcff"],
                "allowed_models": ["comps_multiples"],
                "warnings": ["Enterprise value is not meaningful."],
                "confidence": 0.9,
            }
        )

        # `confidence` is deliberately absent: it is presentation, and hashing it would make
        # a re-run with a slightly different confidence invalidate a live approval.
        assert set(payload) == {
            "sector_key",
            "rationale",
            "proposed_by",
            "blocked_models",
            "allowed_models",
            "warnings",
        }


# -- The SIC proposal ------------------------------------------------------------------------


class TestTheProposalFromSic:
    def test_a_bank_code_proposes_banks(self):
        proposal = propose_from_sic("6021")

        assert proposal.sector_key == "banks"
        assert proposal.is_specialist
        assert "6021" in proposal.rationale

    def test_a_code_that_blocks_no_model_proposes_nothing_and_says_it_matched(self):
        """A gate is earned by blocking a model, not by matching a prefix.

        7372 is the early-stage technology profile's own prefix, and that profile blocks
        nothing — so proposing it would stop a Microsoft run at a gate whose only answers
        are "approve this label" and "kill the run". The match is still recorded, so a
        reviewer sees what the code suggested.
        """
        proposal = propose_from_sic("7372")
        assert proposal.sector_key == ""
        assert not proposal.is_specialist
        assert proposal.sic_candidates == ("early_stage_tech",)
        assert "blocks no valuation model" in proposal.rationale

        ordinary = propose_from_sic("5461")
        assert ordinary.sector_key == ""
        assert not ordinary.is_specialist
        assert ordinary.sic_candidates == ()

    def test_a_missing_code_says_so_rather_than_guessing(self):
        proposal = propose_from_sic("")

        assert proposal.sector_key == ""
        assert "not reported" in proposal.rationale

    def test_the_candidates_are_kept_alongside_the_choice(self):
        """So a reviewer can see whether the classification was obvious or narrow."""
        proposal = propose_from_sic("6021")
        assert proposal.sic_candidates == ("banks",)

    def test_confidence_is_never_certainty(self):
        """A SIC code is self-reported and decades old in places. It proposes; it does not
        decide, and a confidence of 1.0 would invite treating it as though it did."""
        assert propose_from_sic("6021").confidence < 1.0


class TestTheProposalReadsTheCodeInItsOwnScheme:
    """ADR 0121. The scheme is an input to the proposal, not a label added afterwards."""

    def test_a_london_bank_proposes_banks(self):
        proposal = propose_from_sic("64191", scheme=SicScheme.UK_SIC_2007)

        assert proposal.sector_key == "banks"
        assert proposal.is_specialist
        assert proposal.rationale == "UK SIC 2007 64191 matches Banks."

    def test_the_same_digits_propose_differently_in_the_two_schemes(self):
        """The failure this column exists to prevent, at the layer an operator sees.

        `631` under the US scheme proposes insurers, which blocks the discounted cash flow.
        Under the UK scheme it is data processing, which blocks nothing. A London software
        company read with the wrong scheme meets a sector gate asking it to confirm that it
        is an insurer.
        """
        as_us = propose_from_sic("631", scheme=SicScheme.US_SIC)
        as_uk = propose_from_sic("631", scheme=SicScheme.UK_SIC_2007)

        assert as_us.sector_key == "insurers"
        assert as_uk.sector_key == ""
        assert as_uk.sic_candidates == ("early_stage_tech",)

    def test_the_rationale_names_the_scheme_so_a_reader_can_check_the_code(self):
        """ "SIC 64191" sends a reader to the wrong register, where it means nothing."""
        for scheme, expected in (
            (SicScheme.US_SIC, "SIC 6021"),
            (SicScheme.UK_SIC_2007, "UK SIC 2007 64191"),
        ):
            code = "6021" if scheme is SicScheme.US_SIC else "64191"
            assert propose_from_sic(code, scheme=scheme).rationale.startswith(expected)

    def test_an_unseeded_scheme_says_the_gap_is_here_rather_than_in_the_company(self):
        """A scheme with no prefixes anywhere must not read as "an ordinary company".

        This is the shape the UK path would have had if the profiles had been shipped with
        US prefixes only: every London bank proposing nothing, meeting no gate and taking
        the standard model, with a rationale that sounded like a finding about the company.
        """
        stripped = tuple(replace(profile, uk_sic_prefixes=()) for profile in SECTOR_PROFILES)
        proposal = propose_from_sic("64191", scheme=SicScheme.UK_SIC_2007, profiles=stripped)

        assert proposal.sector_key == ""
        assert "gap here rather than a fact about the company" in proposal.rationale


class TestTheClassifyStepReadsTheCompanySOwnScheme:
    """The wiring, at the step that feeds the sector gate.

    Everything above this is arithmetic on constants. This is the one that would still pass
    if `_classify` read the code and dropped the scheme — and a London bank whose scheme was
    dropped meets no gate and takes the standard model, which is ADR 0029's whole subject.
    """

    async def _classification(self, session: Any, scene: dict[str, Any], company: Company) -> dict:
        step = JobStep(
            job_id=scene["job"].id,
            step_key=CLASSIFY_STEP,
            sequence=3,
            idempotency_key=f"{scene['job'].id}:{CLASSIFY_STEP}",
            input_hash="0" * 64,
        )
        context = StepContext(
            session=session,
            job=scene["job"],
            step=step,
            services={},
            outputs={"acquire": {"company_id": str(company.id)}},
        )
        result = await _classify(context)
        return dict(result.output)

    @pytest.fixture
    async def barclays(self, db_session: Any) -> Company:
        company = Company(
            name="Barclays PLC",
            company_number="00048839",
            ticker="BARC",
            exchange="LSE",
            sic="64191",
            sic_description="Banks",
            sic_scheme=SicScheme.UK_SIC_2007,
        )
        db_session.add(company)
        await db_session.flush()
        return company

    async def test_a_london_bank_is_classified_as_a_bank(
        self, db_session: Any, scene: dict[str, Any], barclays: Company
    ) -> None:
        output = await self._classification(db_session, scene, barclays)

        assert output["sector_key"] == "banks"
        assert output["sector_label"] == "Banks"
        assert ValuationModel.DCF_FCFF.value in output["blocked_models"]
        assert output["rationale"] == "UK SIC 2007 64191 matches Banks."

    async def test_the_same_bank_read_as_a_us_filer_is_classified_as_nothing(
        self, db_session: Any, scene: dict[str, Any], barclays: Company
    ) -> None:
        """What dropping the scheme costs, stated as a test rather than as a comment.

        `64191` is not a US SIC code, so the standard model runs on a bank and the gate that
        would have stopped it never fires.
        """
        barclays.sic_scheme = SicScheme.US_SIC
        await db_session.flush()

        output = await self._classification(db_session, scene, barclays)

        assert output["sector_key"] == ""
        assert output["blocked_models"] == []


# -- Required-metric disclosure --------------------------------------------------------------


class TestRequiredMetricsAreDisclosed:
    def test_the_ones_a_run_could_not_compute_are_named(self):
        banks = profile_for("banks")
        assert banks is not None

        disclosure = metric_disclosure(banks, computed=["net_interest_margin", "cet1_ratio"])

        assert disclosure.present == ("net_interest_margin", "cet1_ratio")
        assert disclosure.missing == (
            "cost_income_ratio",
            "loan_loss_provisions",
            "tangible_book_value_per_share",
        )
        assert not disclosure.is_complete

    def test_the_paragraph_names_them_rather_than_counting_them(self):
        banks = profile_for("banks")
        assert banks is not None

        paragraph = metric_disclosure(banks, computed=["cet1_ratio"]).as_paragraph()

        assert "cost_income_ratio" in paragraph
        assert "absent rather than estimated" in paragraph

    def test_a_complete_run_says_so_too(self):
        """Both halves. "Everything required is here" is worth stating."""
        banks = profile_for("banks")
        assert banks is not None

        disclosure = metric_disclosure(banks, computed=banks.required_metrics)

        assert disclosure.is_complete
        assert "Every metric" in disclosure.as_paragraph()

    def test_an_unclassified_company_owes_nothing_specific(self):
        disclosure = metric_disclosure(None, computed=["revenue"])

        assert disclosure.missing == ()
        assert disclosure.as_paragraph() == ""

    def test_the_disclosure_survives_json(self):
        """It goes into a report section's payload, which is JSONB."""
        reits = profile_for("reits")
        assert reits is not None

        stored = metric_disclosure(reits, computed=["ffo"]).as_dict()

        assert stored["sector_key"] == "reits"
        assert "affo" in stored["missing"]
        assert stored["complete"] is False


# -- The gate payload a page reads -----------------------------------------------------------


class TestTheGatePayloadForAPage:
    async def test_it_reads_back_what_the_step_wrote(self, db_session, scene):
        await record_classification(db_session, scene, "banks")

        payload = await sector_service.gate_payload_for_job(db_session, scene["job"].id)

        assert payload["sector_key"] == "banks"
        assert "dcf_fcff" in payload["blocked_models"]

    async def test_a_run_with_no_classification_renders_empty_rather_than_failing(
        self, db_session, scene
    ):
        payload = await sector_service.gate_payload_for_job(db_session, scene["job"].id)
        assert payload == {}


# -- The report -------------------------------------------------------------------------------


class TestTheWarningsReachTheReport:
    """`docs/archive/phase-3-plan.md`: "the seeded warnings reach the report"."""

    def test_the_block_names_the_model_it_did_not_run(self):
        note = SectorNote(
            label="Banks",
            warnings=("Enterprise value is not meaningful for a bank.",),
            blocked_models=("dcf_fcff",),
            metric_disclosure="This report does not carry 5 of the 5 metrics required.",
        )

        block = "\n".join(_sector_block(note))

        assert "## Sector: Banks" in block
        assert "does not run dcf_fcff" in block
        assert "blocked for this sector rather than discouraged" in block

    def test_the_seeded_warnings_appear_verbatim(self):
        banks = profile_for("banks")
        assert banks is not None
        note = SectorNote(label=banks.label, warnings=banks.warnings)

        block = "\n".join(_sector_block(note))

        for warning in banks.warnings:
            assert warning in block

    def test_the_missing_metrics_appear_in_the_block(self):
        banks = profile_for("banks")
        assert banks is not None
        disclosure = metric_disclosure(banks, computed=["cet1_ratio"])
        note = SectorNote(label=banks.label, metric_disclosure=disclosure.as_paragraph())

        block = "\n".join(_sector_block(note))

        assert "loan_loss_provisions" in block

    def test_an_ordinary_company_gets_no_block(self):
        """A report announcing "this is not a bank" on every run trains a reader to skip it."""
        assert _sector_block(None) == []
        assert _sector_block(SectorNote(label="")) == []

    async def test_the_render_step_reads_the_confirmed_classification(self, db_session, scene):
        """Not the proposal. A report may only carry limitations somebody agreed applied."""
        output = await record_classification(db_session, scene, "banks")
        await confirm(db_session, scene, str(output["payload_hash"]))

        profile, _ = await confirmed_classification(db_session, scene["job"])
        assert profile is not None

        note = SectorNote(
            label=profile.label,
            warnings=profile.warnings,
            blocked_models=tuple(m.value for m in profile.blocked_models),
            metric_disclosure=metric_disclosure(profile, computed=[]).as_paragraph(),
        )
        block = "\n".join(_sector_block(note))

        assert "dcf_fcff" in block
        assert "deposits and debt are raw material" in block
        assert "tangible_book_value_per_share" in block


class TestTheBlockIsAtTheTopOfARenderedReport:
    """Rendered end to end, with a body, because position is the whole claim.

    Written after a sabotage run moved the block to the foot of the document and nothing
    failed: every test called `_sector_block` directly, so the *placement* — which is what
    distinguishes a block from a footnote — was asserted nowhere.
    """

    async def test_it_precedes_the_analysis(self, db_session, scene):
        definitions = await resolve_sections(db_session, request=scene["request"])
        await create_report_sections(
            db_session, job_id=scene["job"].id, definitions=definitions[:1]
        )
        rows = await sections_for_job(db_session, scene["job"].id)
        rows[0].status = SectionStatus.GENERATED
        rows[0].content = {
            "thesis": "The analysis a reader remembers.",
            "key_points": ["A point."],
        }
        await db_session.flush()

        banks = profile_for("banks")
        assert banks is not None

        rendered = await render_markdown(
            db_session,
            job=scene["job"],
            request=scene["request"],
            sector=SectorNote(
                label=banks.label,
                warnings=banks.warnings,
                blocked_models=("dcf_fcff",),
                metric_disclosure=metric_disclosure(banks, computed=[]).as_paragraph(),
            ),
        )

        document = rendered.markdown
        assert "## Sector: Banks" in document
        assert document.index("## Sector: Banks") < document.index(
            "The analysis a reader remembers."
        )

    async def test_an_ordinary_report_carries_no_block(self, db_session, scene):
        rendered = await render_markdown(
            db_session, job=scene["job"], request=scene["request"], sector=None
        )

        assert "## Sector:" not in rendered.markdown
