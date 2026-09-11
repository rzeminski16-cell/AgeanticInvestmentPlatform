"""The gate policy, held to what a careful operator would decide, offline."""

from __future__ import annotations

from decimal import Decimal

from audit.driver.policy import (
    SPINE_KEYS,
    FinalGateFacts,
    decide_assumptions,
    decide_final,
    decide_peer_set,
    decide_plan,
    decide_sector,
    decide_unmapped,
)
from audit.subjects import subject_for

MSFT = subject_for("msft1")
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
        verdict = decide_assumptions(payload, MSFT)
        assert verdict.approve
        assert {row.name for row in verdict.supply} == {"risk_free_rate", "equity_risk_premium"}

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

    def test_a_failed_metric_stops(self) -> None:
        facts = FinalGateFacts(
            sections=self._sections(18),
            triggers=(),
            escalations=(),
            revisions=(),
            failed_metrics=("citation_accuracy",),
        )
        assert decide_final(facts).stop_reason == "failed metrics"

    def test_a_look_ahead_trigger_stops(self) -> None:
        facts = FinalGateFacts(
            sections=self._sections(18),
            triggers=({"kind": "look_ahead_detected"},),
            escalations=(),
            revisions=(),
        )
        assert decide_final(facts).stop_reason == "look-ahead trigger"
