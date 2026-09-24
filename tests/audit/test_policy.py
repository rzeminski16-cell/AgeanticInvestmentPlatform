"""The gate policy, held to what a careful operator would decide, offline."""

from __future__ import annotations

from decimal import Decimal

import pytest

from audit.driver.policy import (
    SPINE_KEYS,
    FinalGateFacts,
    GateVerdict,
    decide_assumptions,
    decide_final,
    decide_peer_set,
    decide_plan,
    decide_sector,
    decide_unmapped,
    override_final,
)
from audit.subjects import subject_for

MSFT = subject_for("msft1")
# The round's commissions carry no typed beta and no typed risk-free rate; `msft2` is the
# same company with both stated, which is what a test about supplying stated values needs.
MSFT_TYPED = subject_for("msft2")
MTB = subject_for("mtb")


def _plan(**overrides):
    payload = {
        "section_listing": [{"key": key} for key in SPINE_KEYS],
        "planned_sources": ["SEC EDGAR: FY2025 Form 10-K", "FY2026 Form 10-Q"],
        "estimated_cost_gbp": "9.31",
        "prior_research": "",
        "critique": {"challenges": [{"severity": 2}]},
    }
    payload.update(overrides)
    return payload


class TestThePlanGate:
    def test_a_plan_naming_the_filings_is_approved(self) -> None:
        verdict = decide_plan(_plan(), MSFT, cap_gbp=Decimal("12"))
        assert verdict.approve
        assert verdict.findings == ()

    def test_a_plan_naming_no_filing_stops(self) -> None:
        verdict = decide_plan(_plan(planned_sources=["Wikipedia"]), MSFT, cap_gbp=Decimal("12"))
        assert not verdict.approve
        assert verdict.stop_reason == "plan names no filing"

    def test_a_missing_section_is_a_finding_not_a_stop(self) -> None:
        listing = [{"key": key} for key in SPINE_KEYS if key != "catalysts"]
        verdict = decide_plan(_plan(section_listing=listing), MSFT, cap_gbp=Decimal("12"))
        assert verdict.approve
        assert any("catalysts" in finding for finding in verdict.findings)

    def test_the_refresh_run_expects_prior_research(self) -> None:
        refresh = subject_for("msft2")
        verdict = decide_plan(_plan(), refresh, cap_gbp=Decimal("12"))
        assert any("saw no prior research" in f for f in verdict.findings)

    def test_an_estimate_above_the_cap_is_a_finding(self) -> None:
        verdict = decide_plan(_plan(estimated_cost_gbp="13.00"), MSFT, cap_gbp=Decimal("12"))
        assert verdict.approve
        assert any("exceeds the request cap" in f for f in verdict.findings)


class TestTheSectorGate:
    def test_the_bank_is_expected_to_be_a_bank(self) -> None:
        verdict = decide_sector({"sector_key": "banks", "warnings": []}, MTB)
        assert verdict.approve

    def test_an_ordinary_company_proposed_as_a_bank_stops(self) -> None:
        verdict = decide_sector({"sector_key": "banks"}, MSFT)
        assert not verdict.approve
        assert verdict.stop_reason == "unexpected sector proposal"


class TestThePeerGate:
    def test_the_subject_in_its_own_slate_is_a_finding(self) -> None:
        payload = {
            "peers": [{"identifier": "MSFT", "name": "Microsoft Corporation"}],
            "proposed_by": "model",
        }
        verdict = decide_peer_set(payload, MSFT)
        assert verdict.approve
        assert any("own peer set" in f for f in verdict.findings)


class TestTheUnmappedGate:
    def test_zero_facts_stops(self) -> None:
        verdict = decide_unmapped({"unmapped_tags": [], "load_errors": []}, MSFT, facts_chosen=0)
        assert not verdict.approve

    def test_a_large_unmapped_line_is_recorded(self) -> None:
        payload = {
            "unmapped_tags": ["msft:Something"],
            "unmapped_concepts": [
                {"tag": "msft:Something", "share": "0.12", "reference": "revenue"}
            ],
            "load_errors": [],
        }
        verdict = decide_unmapped(payload, MSFT, facts_chosen=500)
        assert verdict.approve
        assert any("12%" in f for f in verdict.findings)


class TestTheAssumptionsGate:
    def test_outstanding_operator_names_are_supplied(self) -> None:
        payload = {
            "assumptions": [
                {
                    "name": "terminal_growth",
                    "value": "0.025",
                    "unit": "pure",
                    "proposed_by": "model",
                }
            ],
            "outstanding": [
                {"name": "risk_free_rate", "reason": "no series"},
                {"name": "equity_risk_premium", "reason": "judgement"},
            ],
            "refused": [],
            "skipped": [],
        }
        verdict = decide_assumptions(payload, MSFT_TYPED)
        assert verdict.approve
        assert {row.name for row in verdict.supply} == {"risk_free_rate", "equity_risk_premium"}

    def test_a_subject_with_no_stated_rate_stops_rather_than_falling_back(self) -> None:
        """The measurement round's commissions, and why they were stripped (`bbfc827`).

        `msft1` and `azn` carry no typed beta and no typed risk-free rate, because the
        policy supplies a stated value for anything the gate lists outstanding — so a
        derivation that silently produced nothing was silently replaced by a guess, and
        September's approved MSFT run valued on a hand-typed beta of 0.900 where the
        platform's own regression said 1.068. With nothing to fall back on the run stops
        and names the input, which is a finding rather than a number.
        """
        payload = {
            "assumptions": [],
            "outstanding": [{"name": "risk_free_rate", "reason": "no series"}],
            "refused": [],
            "skipped": [],
        }
        verdict = decide_assumptions(payload, MSFT)

        assert not verdict.approve
        assert "risk_free_rate" in (verdict.stop_reason or "")

    def test_an_outstanding_name_nobody_stated_stops(self) -> None:
        payload = {"assumptions": [], "outstanding": [{"name": "payout_ratio", "reason": "?"}]}
        verdict = decide_assumptions(payload, MSFT)
        assert not verdict.approve
        assert "payout_ratio" in (verdict.stop_reason or "")

    def test_a_value_outside_the_band_is_amended_to_the_bound(self) -> None:
        payload = {
            "assumptions": [
                {"name": "terminal_growth", "value": "0.06", "unit": "pure", "proposed_by": "model"}
            ],
            "outstanding": [],
        }
        verdict = decide_assumptions(payload, MSFT)
        assert verdict.approve
        assert verdict.amend[0].name == "terminal_growth"
        assert verdict.amend[0].value == Decimal("0.035")

    def test_percent_units_are_read_as_fractions(self) -> None:
        payload = {
            "assumptions": [{"name": "beta", "value": "90", "unit": "percent"}],
            "outstanding": [],
        }
        verdict = decide_assumptions(payload, MSFT)
        assert verdict.amend == ()


class TestTheFinalGate:
    def _sections(self, generated: int, failed: int = 0) -> tuple[dict, ...]:
        rows = [{"key": f"s{i}", "status": "generated", "note": None} for i in range(generated)]
        rows += [{"key": f"f{i}", "status": "failed", "note": "refused"} for i in range(failed)]
        return tuple(rows)

    def test_a_whole_draft_with_passing_metrics_is_approved(self) -> None:
        facts = FinalGateFacts(
            sections=self._sections(18), triggers=(), escalations=(), revisions=()
        )
        assert decide_final(facts).approve

    def test_two_lost_sections_stop(self) -> None:
        facts = FinalGateFacts(
            sections=self._sections(16, failed=2), triggers=(), escalations=(), revisions=()
        )
        verdict = decide_final(facts)
        assert not verdict.approve
        assert verdict.stop_reason == "too many sections lost"

    def test_a_whole_quick_draft_is_approved(self) -> None:
        """QUICK mode writes nine sections, and nine of nine is a whole report.

        The floor was a fixed seventeen — all but one of the eighteen a standard run
        writes — so the first live QUICK run generated every section it had and the policy
        called it "too many sections lost". The tolerance is one lost section, counted
        against the sections the run actually has.
        """
        facts = FinalGateFacts(
            sections=self._sections(9), triggers=(), escalations=(), revisions=()
        )
        assert decide_final(facts).approve

    def test_a_quick_draft_that_lost_two_still_stops(self) -> None:
        """The tolerance travels with the mode; it does not disappear with the floor."""
        facts = FinalGateFacts(
            sections=self._sections(7, failed=2), triggers=(), escalations=(), revisions=()
        )
        assert decide_final(facts).stop_reason == "too many sections lost"

    def test_a_failed_metric_stops(self) -> None:
        facts = FinalGateFacts(
            sections=self._sections(18),
            triggers=(),
            escalations=(),
            revisions=(),
            failed_metrics=("citation_accuracy",),
        )
        assert decide_final(facts).stop_reason == "failed metrics"


class TestTheOperatorsOverride:
    """A final gate the policy stops at on failed checks can be approved by the operator's
    decision, with the reason on the row; nothing else it stops at can."""

    def _stopped(
        self, *, lost: int = 0, failed_metrics: tuple[str, ...] = ("presentation_integrity",)
    ) -> GateVerdict:
        sections = tuple(
            {"key": f"s{i}", "status": "failed" if i < lost else "generated", "note": None}
            for i in range(18)
        )
        return decide_final(
            FinalGateFacts(
                sections=sections,
                triggers=(),
                escalations=(),
                revisions=(),
                failed_metrics=failed_metrics,
            )
        )

    def test_failed_checks_are_approved_with_both_reasons_on_the_row(self) -> None:
        verdict = override_final(self._stopped(), "Published so the round has its document.")

        assert verdict.approve
        assert verdict.rationale.startswith(
            "Operator override: Published so the round has its document."
        )
        assert "presentation_integrity" in verdict.rationale
        assert verdict.findings[-1] == "approved against the policy's stop, by the operator"

    def test_a_draft_that_lost_sections_is_not_overridden(self) -> None:
        stopped = self._stopped(lost=2, failed_metrics=())

        with pytest.raises(ValueError, match="no reason answers"):
            override_final(stopped, "Publish it anyway.")

    def test_a_blank_reason_is_refused(self) -> None:
        with pytest.raises(ValueError, match="a click"):
            override_final(self._stopped(), "  ")

    def test_an_approved_verdict_passes_through_untouched(self) -> None:
        approved = self._stopped(failed_metrics=())

        assert approved.approve
        assert override_final(approved, "Not needed.") is approved
