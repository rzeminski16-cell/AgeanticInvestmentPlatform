"""The seven gates' shared guarantees, driven by a real browser (tranche 6).

What only a browser can prove about the frame all seven gates share: that a stale payload
is refused and records nothing, that a decided gate offers no form at all, that the journey
never draws a conditional stop as a promise, that the assumptions gate's row forms are
siblings whose submission decides no gate, and that the rationale gates render their
reasons at full length. The unit halves — the journey's arithmetic, the verdict's schema —
live in `test_web_gates.py` and `test_verdict_step.py`.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import Page, expect

from aer.core.enums import GateKind, JobStatus
from tests.e2e.test_run_console import RunFixture

pytestmark = [pytest.mark.e2e, pytest.mark.integration]

CONSOLE_URL = re.compile(r"/runs/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


@pytest.fixture
def waiting_run(live_server: str, database_url: str) -> RunFixture:
    """A run stopped at gate 1, in the server the browser will talk to."""
    run = RunFixture(database_url)
    assert run.advance() is JobStatus.AWAITING_APPROVAL
    return run


class TestTheSharedFrame:
    def test_a_stale_payload_is_refused_and_records_nothing(
        self, page: Page, live_server: str, waiting_run: RunFixture
    ) -> None:
        """The guarantee the decision panel prints, exercised rather than trusted.

        If the proposal changed between the page being served and the button being
        pressed, the approval must be refused — an approval of something else is not an
        approval of this. Tampering with the hidden field is the cheapest way to stand
        somewhere stale.
        """
        page.goto(f"{live_server}/runs/{waiting_run.job_id}/plan")
        page.evaluate("document.getElementById('payload-hash').value = 'a'.repeat(64)")

        page.click("#approve")

        expect(page.locator("#problem")).to_be_visible()
        # Nothing was recorded: the gate is still live on a fresh render.
        page.goto(f"{live_server}/runs/{waiting_run.job_id}/plan")
        expect(page.locator("#gate-form")).to_be_visible()
        expect(page.locator("#already-decided")).to_have_count(0)

    def test_a_decided_gate_names_the_decision_and_renders_no_form(
        self, page: Page, live_server: str, waiting_run: RunFixture
    ) -> None:
        page.goto(f"{live_server}/runs/{waiting_run.job_id}/plan")
        page.click("#approve")
        page.wait_for_url(CONSOLE_URL)

        page.goto(f"{live_server}/runs/{waiting_run.job_id}/plan")

        expect(page.locator("#already-decided")).to_be_visible()
        expect(page.locator("#already-decided")).to_contain_text("Approved by")
        expect(page.locator("#gate-form")).to_have_count(0)
        expect(page.locator("#approve")).to_have_count(0)

    def test_the_journey_never_promises_a_conditional_stop(
        self, page: Page, live_server: str, waiting_run: RunFixture
    ) -> None:
        """Five of the seven gates depend on what the company turns out to be, and a
        journey that drew them as steps to come would be a lie about the workflow."""
        page.goto(f"{live_server}/runs/{waiting_run.job_id}/plan")

        expect(page.locator('[data-journey-state="current"]')).to_have_count(1)
        expect(page.locator('[data-journey-state="upcoming"]')).to_have_count(1)
        expect(page.locator('[data-journey-state="possible"]')).to_have_count(5)
        expect(page.get_by_text("Between two and seven more decisions.")).to_be_visible()

    def test_the_full_fingerprint_is_kept_copyable(
        self, page: Page, live_server: str, waiting_run: RunFixture
    ) -> None:
        """Abbreviated visually, never withheld: the disclosure holds all 64 characters."""
        page.goto(f"{live_server}/runs/{waiting_run.job_id}/plan")

        hidden = page.locator("#payload-hash").get_attribute("value")
        assert hidden is not None
        assert len(hidden) == 64
        page.get_by_text("What this decision proves").click()
        expect(page.get_by_text(hidden)).to_be_visible()


class TestTheAssumptionsGate:
    @pytest.fixture
    def at_assumptions(self, live_server: str, database_url: str) -> RunFixture:
        run = RunFixture(database_url)
        assert run.advance_until(GateKind.ASSUMPTIONS) is JobStatus.AWAITING_APPROVAL
        return run

    def test_row_forms_are_siblings_and_confirming_decides_no_gate(
        self, page: Page, live_server: str, at_assumptions: RunFixture
    ) -> None:
        """Confirming a value and deciding the gate are two acts, structurally.

        The browser's parser flattens a nested form silently, so the honest proof is
        behavioural: a row confirmation lands back on the gate with the gate still
        undecided and the row now confirmed.
        """
        page.goto(f"{live_server}/runs/{at_assumptions.job_id}/assumptions")

        expect(page.locator("#gate-form form")).to_have_count(0)
        rows = page.locator("#proposed-assumptions li")
        expect(rows.first).to_be_visible()

        page.get_by_role("button", name="Confirm this value").first.click()

        page.wait_for_url(re.compile(r"/assumptions$"))
        expect(page.locator("#gate-form")).to_be_visible(), "the gate is still undecided"
        expect(page.locator("#already-decided")).to_have_count(0)
        expect(page.get_by_text("Confirmed", exact=True).first).to_be_visible()

    def test_the_gate_renders_from_live_rows(
        self, page: Page, live_server: str, at_assumptions: RunFixture
    ) -> None:
        """A value confirmed on the page is confirmed on the page's next render — the
        rows are the truth, never the step's frozen output (gap A52)."""
        page.goto(f"{live_server}/runs/{at_assumptions.job_id}/assumptions")
        before = page.locator('text="Not yet confirmed"').count()
        assert before >= 1, "the scene proposes at least one unconfirmed value"

        page.get_by_role("button", name="Confirm this value").first.click()
        page.wait_for_url(re.compile(r"/assumptions$"))

        expect(page.locator('text="Not yet confirmed"')).to_have_count(before - 1)


class TestTheRationaleGates:
    def test_the_peer_rationale_is_rendered_at_full_length(
        self, page: Page, live_server: str, database_url: str
    ) -> None:
        run = RunFixture(database_url, subscribed=True)
        assert run.advance_until(GateKind.PEER_SET) is JobStatus.AWAITING_APPROVAL

        page.goto(f"{live_server}/runs/{run.job_id}/peers")

        expect(
            page.get_by_text("Scripted proposal: sells comparable software to comparable buyers.")
        ).to_be_visible()

    def test_the_theme_rationale_is_rendered_at_full_length(
        self, page: Page, live_server: str, database_url: str
    ) -> None:
        run = RunFixture(database_url)
        assert run.advance_until(GateKind.THEME_SET) is JobStatus.AWAITING_APPROVAL

        page.goto(f"{live_server}/runs/{run.job_id}/themes")

        expect(
            page.get_by_text(
                "Scripted proposal: the subject sits squarely in this recurring story."
            )
        ).to_be_visible()


class TestTheReviewVerdict:
    def test_the_two_halves_render_and_the_authored_is_labelled(
        self, page: Page, live_server: str, database_url: str
    ) -> None:
        """ADR 0087 on the page: composed counts lead, the model's sentence sits beside
        them as interpretation, and neither replaces the other."""
        run = RunFixture(database_url)
        assert run.advance_until(GateKind.FINAL) is JobStatus.AWAITING_APPROVAL

        page.goto(f"{live_server}/runs/{run.job_id}/review")

        expect(page.get_by_text("THE REVIEW")).to_be_visible()
        expect(page.get_by_text(re.compile(r"of £\d.*spent")).first).to_be_visible()
        expect(
            page.get_by_text("Scripted verdict; the record reads complete and unchallenged.")
        ).to_be_visible()
        expect(page.get_by_text("interpretation, never evidence")).to_be_visible()
