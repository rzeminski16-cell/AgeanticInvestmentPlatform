"""The concept-map curation worksheet — roadmap §2.8's mechanism.

A55 has survived several passes because the work is judgement over accounting semantics
and nothing can do it for the operator. What a session can do is prepare the sitting, and
the tests are about the two ways that preparation could waste one: **ranking that buries
the row that matters**, and **putting a settled question back in front of somebody** whose
whole job in this file is to decide what things mean.

The aggregation is pure, so most of this runs without a database.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from aer.core.concepts import CANONICAL_CONCEPTS
from aer.core.enums import JobStatus
from aer.db.models import JobStep
from aer.services.curation import curation_worksheet, render_worksheet, worksheet_rows
from tests.workflow_fixtures import seed_job, seed_request, seed_user

REFUSED_TAG = (
    "us-gaap:ShareBasedCompensationArrangementByShareBasedPaymentAward"
    "FairValueAssumptionsRiskFreeInterestRate"
)


def row(tag: str, *, share: str = "", value: str = "", **extra: object) -> dict[str, object]:
    return {
        "tag": tag,
        "label": extra.get("label", ""),
        "share": share,
        "value": value,
        "unit": extra.get("unit", "USD"),
        "period_end": extra.get("period_end", "2025-12-31"),
        "observations": extra.get("observations", 1),
    }


def run(*rows: dict[str, object]) -> dict[str, object]:
    return {"unmapped_concepts": list(rows), "unmapped_tags": [r["tag"] for r in rows]}


class TestTheRankingPutsTheDecisionFirst:
    def test_the_biggest_share_leads(self) -> None:
        sheet = worksheet_rows(
            [run(row("us-gaap:Small", share="0.004"), row("us-gaap:Large", share="0.31"))]
        )

        assert [r.tag for r in sheet.rows] == ["us-gaap:Large", "us-gaap:Small"]

    def test_the_largest_share_across_runs_wins(self) -> None:
        """A tag that was 0.4% of one company's revenue and 31% of another's is a 31%
        decision: the sitting is looking for what would most change a report."""
        sheet = worksheet_rows(
            [
                run(row("us-gaap:Swings", share="0.004")),
                run(row("us-gaap:Swings", share="0.31")),
                run(row("us-gaap:Steady", share="0.05")),
            ]
        )

        assert [r.tag for r in sheet.rows] == ["us-gaap:Swings", "us-gaap:Steady"]
        assert sheet.rows[0].largest_share == Decimal("0.31")

    def test_a_tag_nothing_could_scale_sorts_after_the_scaled_ones(self) -> None:
        """Unscalable is not zero, and it must not be ranked as though somebody had
        measured it and found nothing."""
        sheet = worksheet_rows([run(row("us-gaap:Unscaled"), row("us-gaap:Tiny", share="0.0001"))])

        assert [r.tag for r in sheet.rows] == ["us-gaap:Tiny", "us-gaap:Unscaled"]
        assert sheet.rows[1].largest_share is None
        assert sheet.rows[1].share_text == "—"

    def test_how_many_runs_met_a_tag_is_counted(self) -> None:
        """A tag one run saw once is a different decision from one every run meets."""
        sheet = worksheet_rows(
            [run(row("us-gaap:Common", share="0.02")), run(row("us-gaap:Common", share="0.03"))]
        )

        [only] = sheet.rows
        assert only.runs == 2

    def test_the_largest_figure_is_kept_with_its_period(self) -> None:
        sheet = worksheet_rows(
            [
                run(row("us-gaap:Grows", share="0.01", value="12", period_end="2024-12-31")),
                run(row("us-gaap:Grows", share="0.02", value="900", period_end="2025-12-31")),
            ]
        )

        [only] = sheet.rows
        assert only.largest_value == "900"
        assert only.period_end == "2025-12-31"


class TestASettledQuestionIsNotAskedAgain:
    """Roadmap §2.7 meeting §2.8. The one person who could map a refused tag by mistake is
    the person doing this sitting, and a worksheet that listed it would be handing them the
    decision the refusal exists to prevent."""

    def test_a_refused_tag_is_not_a_row_to_decide(self) -> None:
        sheet = worksheet_rows(
            [run(row(REFUSED_TAG, share="0.9"), row("us-gaap:Real", share="0.1"))]
        )

        assert [r.tag for r in sheet.rows] == ["us-gaap:Real"]

    def test_it_is_still_listed_with_its_reason(self) -> None:
        """Excluded from the sitting, not hidden: the worksheet is a complete account of
        what the runs met."""
        sheet = worksheet_rows([run(row(REFUSED_TAG, share="0.9"))])

        [(tag, reason)] = sheet.refused
        assert tag == REFUSED_TAG
        assert "government-yield series" in reason

    def test_the_document_says_it_asks_nothing(self) -> None:
        document = render_worksheet(worksheet_rows([run(row(REFUSED_TAG), row("us-gaap:Real"))]))

        assert "Already refused — nothing to decide here" in document
        assert "not so they are reconsidered" in document


class TestTheDocumentIsSomethingAPersonCanFillIn:
    def test_it_carries_a_column_for_the_answer_and_one_for_the_reasoning(self) -> None:
        document = render_worksheet(worksheet_rows([run(row("us-gaap:Real", share="0.2"))]))

        assert "| Maps to | Why |" in document
        assert "Leave `Maps to` blank for a tag that should stay unmapped" in document

    def test_it_lists_the_vocabulary_a_tag_may_map_onto(self) -> None:
        """A curator choosing a target needs the closed list in front of them; guessing a
        concept name produces an alias pointing at nothing, which is silent."""
        document = render_worksheet(worksheet_rows([run(row("us-gaap:Real"))]))

        assert "The vocabulary a tag may map onto" in document
        assert "`revenue`" in document
        assert len(CANONICAL_CONCEPTS) >= 60

    def test_it_says_how_many_runs_it_read(self) -> None:
        document = render_worksheet(worksheet_rows([run(row("a")), run(row("b"))]))

        assert "Prepared from 2 recorded runs" in document

    def test_a_run_recorded_before_the_figures_existed_still_contributes(self) -> None:
        """Older step outputs carry the bare tag list. Those tags belong in the sitting;
        they arrive with nothing to rank them by, which the row says rather than hides."""
        sheet = worksheet_rows([{"unmapped_tags": ["us-gaap:Ancient"], "unmapped_concepts": []}])

        [only] = sheet.rows
        assert only.tag == "us-gaap:Ancient"
        assert only.largest_share is None

    def test_nothing_to_decide_produces_no_rows_rather_than_an_empty_table(self) -> None:
        sheet = worksheet_rows([])

        assert sheet.rows == ()
        assert sheet.runs_read == 0


@pytest.mark.integration
class TestItReadsWhatTheRunsRecorded:
    async def test_the_worksheet_comes_from_the_extract_steps_own_rows(
        self, db_session, workflow_settings
    ) -> None:
        """Deliberately the recorded output rather than a re-derivation: the worksheet
        describes what the operator was actually shown at the gate."""
        user = await seed_user(db_session)
        request = await seed_request(db_session, user=user)
        job = await seed_job(db_session, request=request)
        db_session.add(
            JobStep(
                job_id=job.id,
                step_key="extract",
                sequence=0,
                attempt=0,
                idempotency_key=f"{job.id}:extract",
                input_hash="0" * 64,
                status=JobStatus.SUCCEEDED,
                output_ref=run(row("us-gaap:FromTheRun", share="0.42")),
            )
        )
        await db_session.flush()

        sheet = await curation_worksheet(db_session)

        assert [r.tag for r in sheet.rows] == ["us-gaap:FromTheRun"]
        assert sheet.rows[0].largest_share == Decimal("0.42")
