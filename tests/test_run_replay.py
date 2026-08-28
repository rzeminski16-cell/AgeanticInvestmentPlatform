"""Reproducing a run from its own record, and noticing when it no longer reproduces.

Gap A12. The trap in a replay is the same as in any verifier: one that always says "yes" is
indistinguishable from a reproducible run right up to the audit. So each leg here is broken
on its own — a calculation edited, an excerpt that is no longer in the filing, an artefact
deleted from the store, an exchange never archived — and each must sink the replay by
itself, because in an audit any one of them is enough.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.config import Settings
from aer.core.enums import ClaimKind, JobStatus, Provider, SourceTier
from aer.db.models import AgentRun, Artefact, Calculation, JobStep, SourceDocument
from aer.services.citations import record_citation, record_claim
from aer.services.run_replay import replay_run
from aer.storage.local import LocalArtefactStore
from tests.scene_fixtures import build_scene

pytestmark = pytest.mark.integration


@pytest.fixture
def settings(tmp_path: Any) -> Settings:
    return Settings(
        http_user_agent="Tracework Test test@example.invalid",
        artefact_root=tmp_path / "artefacts",
    )


@pytest.fixture
def store(settings: Settings) -> LocalArtefactStore:
    return LocalArtefactStore(settings.artefact_root, max_bytes=settings.max_artefact_bytes)


@pytest.fixture
async def scene(db_session: AsyncSession, store: LocalArtefactStore) -> dict[str, Any]:
    """A run with an archived filing, an excerpt, a cited claim and an archived exchange."""
    built = await build_scene(db_session, store)

    claim = await record_claim(
        db_session,
        section=built["section"],
        kind=ClaimKind.FACTUAL,
        text="Revenue grew year on year.",
    )
    built["citation"] = await record_citation(
        db_session,
        claim=claim,
        source_document_id=built["document"].id,
        extraction_id=built["extraction"].id,
    )

    step = JobStep(
        job_id=built["job"].id,
        step_key="draft",
        sequence=1,
        status=JobStatus.SUCCEEDED,
        attempt=0,
        idempotency_key=f"{built['job'].id}:draft",
        input_hash="0" * 64,
        started_at=datetime.now(UTC),
    )
    db_session.add(step)
    await db_session.flush()

    # A real exchange archives both halves as artefacts. The scene's filing artefact stands
    # in for them: what the replay checks is that the references are there and resolve.
    built["agent_run"] = AgentRun(
        job_step_id=step.id,
        agent_role="report_writer",
        provider="fake",
        model="claude-sonnet-5",
        request_payload_ref=built["artefact"].id,
        response_payload_ref=built["artefact"].id,
        input_tokens=10,
        output_tokens=20,
    )
    db_session.add(built["agent_run"])
    await db_session.flush()
    return built


class TestARunThatStillHolds:
    async def test_a_sound_run_reproduces(
        self, db_session: AsyncSession, scene: dict[str, Any], settings: Settings
    ) -> None:
        report = await replay_run(
            db_session, scene["store"], job_id=scene["job"].id, settings=settings
        )

        assert report.reproduces, report.problems()
        assert report.citations_checked == 1
        assert report.artefacts_checked == 1
        assert report.model_calls_checked == 1

    async def test_counts_and_failures_are_reported_apart(
        self, db_session: AsyncSession, scene: dict[str, Any], settings: Settings
    ) -> None:
        """ "Nothing failed" and "nothing was checked" must not be the same answer.

        A replay that only reported failures would call a run with no citations at all
        perfectly reproducible, which is the reading an auditor would most like to catch.
        """
        report = await replay_run(
            db_session, scene["store"], job_id=scene["job"].id, settings=settings
        )

        assert report.checked > 0
        assert report.problems() == ()

    async def test_a_job_with_nothing_recorded_checks_nothing(
        self, db_session: AsyncSession, store: LocalArtefactStore, settings: Settings
    ) -> None:
        """Reported honestly as zero checked rather than as a clean bill of health."""
        report = await replay_run(db_session, store, job_id=uuid.uuid4(), settings=settings)

        assert report.checked == 0
        assert report.reproduces


class TestEachLegCanSinkIt:
    async def test_an_excerpt_no_longer_in_the_filing_fails_the_replay(
        self, db_session: AsyncSession, scene: dict[str, Any], settings: Settings
    ) -> None:
        scene["extraction"].excerpt = "Total revenue was $250,000 million for fiscal year 2022."
        await db_session.flush()

        report = await replay_run(
            db_session, scene["store"], job_id=scene["job"].id, settings=settings
        )

        assert not report.reproduces
        assert report.citations_failed
        assert any("citation" in p for p in report.problems())

    async def test_an_artefact_gone_from_the_store_fails_the_replay(
        self, db_session: AsyncSession, scene: dict[str, Any], settings: Settings
    ) -> None:
        """The bytes a citation points at, not merely the row that names them."""
        path = scene["store"].path_for(scene["artefact"].sha256)
        path.unlink()

        report = await replay_run(
            db_session, scene["store"], job_id=scene["job"].id, settings=settings
        )

        assert not report.reproduces
        assert report.artefacts_unreadable == (scene["artefact"].sha256,)

    async def test_a_corrupted_artefact_fails_the_replay(
        self, db_session: AsyncSession, scene: dict[str, Any], settings: Settings
    ) -> None:
        """Present is not the same as intact, which is why this reads rather than stats."""
        path = scene["store"].path_for(scene["artefact"].sha256)
        path.write_bytes(b"different bytes entirely")

        report = await replay_run(
            db_session, scene["store"], job_id=scene["job"].id, settings=settings
        )

        assert not report.reproduces
        assert report.artefacts_unreadable == (scene["artefact"].sha256,)

    async def test_an_uncited_artefact_going_missing_fails_the_replay_on_its_own(
        self, db_session: AsyncSession, scene: dict[str, Any], settings: Settings
    ) -> None:
        """The artefact leg, isolated so that only it can be the cause.

        Deleting the *cited* filing fails the citation check too, because verification
        re-reads those same bytes — so a replay that ignored the artefact leg entirely would
        still come out false, and the test would pass for the wrong reason. A second source
        document that nothing quotes has no other leg standing behind it.
        """
        second = await scene["store"].put_bytes(b"<html><body>A second filing.</body></html>")
        artefact = Artefact(
            sha256=second.sha256,
            media_type="text/html",
            size_bytes=second.size_bytes,
            storage_key=scene["store"].storage_key_for(second.sha256),
        )
        db_session.add(artefact)
        await db_session.flush()
        db_session.add(
            SourceDocument(
                work_order_id=scene["request"].id,
                request_id=scene["request"].id,
                job_id=scene["job"].id,
                artefact_id=artefact.id,
                url="https://www.sec.gov/Archives/edgar/data/789019/second.htm",
                provider=Provider.SEC_EDGAR,
                source_tier=SourceTier.T1_REGULATORY,
                retrieved_at=datetime.now(UTC),
                quarantined=False,
            )
        )
        await db_session.flush()
        scene["store"].path_for(second.sha256).unlink()

        report = await replay_run(
            db_session, scene["store"], job_id=scene["job"].id, settings=settings
        )

        assert report.citations_failed == (), "the cited filing is untouched"
        assert report.artefacts_unreadable == (second.sha256,)
        assert not report.reproduces

    async def test_a_model_call_with_no_archived_exchange_fails_the_replay(
        self, db_session: AsyncSession, scene: dict[str, Any], settings: Settings
    ) -> None:
        """Prose in the report and no record of the exchange that produced it."""
        scene["agent_run"].response_payload_ref = None
        await db_session.flush()

        report = await replay_run(
            db_session, scene["store"], job_id=scene["job"].id, settings=settings
        )

        assert not report.reproduces
        assert len(report.model_calls_unarchived) == 1
        assert "report_writer" in report.model_calls_unarchived[0]

    async def test_a_calculation_edited_after_the_fact_fails_the_replay(
        self, db_session: AsyncSession, scene: dict[str, Any], settings: Settings
    ) -> None:
        """The ledger says one thing; re-running the recorded formula says another."""
        db_session.add(_stored_cagr(scene["job"].id, output=Decimal("0.5")))
        await db_session.flush()

        report = await replay_run(
            db_session, scene["store"], job_id=scene["job"].id, settings=settings
        )

        assert not report.reproduces
        assert report.calculations_diverged

    async def test_the_reason_travels_with_the_divergence(
        self, db_session: AsyncSession, scene: dict[str, Any], settings: Settings
    ) -> None:
        """A divergence says what went wrong, not merely that something did.

        The line used to read "calculation cagr#1 does not replay" and stop there, so a
        record that no longer runs at all and one that moved in the twelfth decimal place
        were the same sentence on the only surface that reports either.
        """
        db_session.add(_stored_cagr(scene["job"].id, output=Decimal("0.5")))
        await db_session.flush()

        report = await replay_run(
            db_session, scene["store"], job_id=scene["job"].id, settings=settings
        )

        assert not report.reproduces
        (problem,) = [line for line in report.problems() if line.startswith("calculation ")]
        assert "cagr#1" in problem
        assert "stored 0.5" in problem
        assert "replayed 0.1" in problem

    async def test_one_broken_leg_is_enough(
        self, db_session: AsyncSession, scene: dict[str, Any], settings: Settings
    ) -> None:
        """Three legs sound and one broken must not average out to reproducible."""
        scene["agent_run"].request_payload_ref = None
        await db_session.flush()

        report = await replay_run(
            db_session, scene["store"], job_id=scene["job"].id, settings=settings
        )

        assert report.citations_checked == 1
        assert not report.citations_failed
        assert report.artefacts_checked == 1
        assert not report.artefacts_unreadable
        assert not report.reproduces


class TestTheStoredFigureIsARoundedOne:
    """`output_value` is `NUMERIC(38, 12)`, and the comparison has to know that.

    An exact comparison called every non-terminating quotient a divergence: 113 of the
    2026-08-24 MSFT run's 1,034 calculations "did not replay" while the evaluation gate
    passed `numerical_consistency` on the same rows, and every one that survived was a sum.
    Both cannot be right. The gate is, and this is the class that keeps the two agreeing.
    """

    async def test_a_ratio_rounded_by_its_column_still_reproduces(
        self, db_session: AsyncSession, scene: dict[str, Any], settings: Settings
    ) -> None:
        """The stored twelve places against a replay carrying the full context precision."""
        db_session.add(
            Calculation(
                job_id=scene["job"].id,
                name="gross_margin",
                sequence=1,
                formula="gross_profit / revenue",
                function_ref="aer.calc.ratios:gross_margin",
                inputs=[
                    _input("gross_profit", "225500"),
                    _input("revenue", "331839"),
                ],
                parameters={},
                # 0.6795464065405211563438896573338275 as the column keeps it.
                output_value=Decimal("0.679546406541"),
                output_unit="pure",
                code_version="test",
            )
        )
        await db_session.flush()

        report = await replay_run(
            db_session, scene["store"], job_id=scene["job"].id, settings=settings
        )

        assert report.calculations_checked == 1
        assert not report.calculations_diverged
        assert report.reproduces

    async def test_a_figure_wrong_beyond_the_rounding_still_diverges(
        self, db_session: AsyncSession, scene: dict[str, Any], settings: Settings
    ) -> None:
        """Tolerating the column's rounding must not tolerate a wrong number.

        A percentage point out on a margin is four hundred million dollars on that revenue,
        and a tolerance that swallowed it would have made the whole check decorative.
        """
        db_session.add(
            Calculation(
                job_id=scene["job"].id,
                name="gross_margin",
                sequence=1,
                formula="gross_profit / revenue",
                function_ref="aer.calc.ratios:gross_margin",
                inputs=[
                    _input("gross_profit", "225500"),
                    _input("revenue", "331839"),
                ],
                parameters={},
                output_value=Decimal("0.689546406541"),
                output_unit="pure",
                code_version="test",
            )
        )
        await db_session.flush()

        report = await replay_run(
            db_session, scene["store"], job_id=scene["job"].id, settings=settings
        )

        assert report.calculations_diverged
        assert not report.reproduces

    async def test_the_same_digits_in_another_unit_are_not_a_reproduction(
        self, db_session: AsyncSession, scene: dict[str, Any], settings: Settings
    ) -> None:
        """0.68 pure and 0.68 USD are the same digits and different claims."""
        db_session.add(
            Calculation(
                job_id=scene["job"].id,
                name="gross_margin",
                sequence=1,
                formula="gross_profit / revenue",
                function_ref="aer.calc.ratios:gross_margin",
                inputs=[
                    _input("gross_profit", "225500"),
                    _input("revenue", "331839"),
                ],
                parameters={},
                output_value=Decimal("0.679546406541"),
                output_unit="USD",
                code_version="test",
            )
        )
        await db_session.flush()

        report = await replay_run(
            db_session, scene["store"], job_id=scene["job"].id, settings=settings
        )

        (problem,) = [line for line in report.problems() if line.startswith("calculation ")]
        assert "replayed in pure, stored USD" in problem


def _input(name: str, value: str) -> dict[str, Any]:
    return {
        "name": name,
        "value": value,
        "unit": "USD",
        "source": {"kind": "fact", "id": str(uuid.uuid4())},
    }


def _stored_cagr(job_id: uuid.UUID, *, output: Decimal) -> Calculation:
    """A compound growth rate whose true answer is exactly 0.1."""
    return Calculation(
        job_id=job_id,
        name="cagr",
        sequence=1,
        formula="(end / start) ** (1 / years) - 1",
        function_ref="aer.calc.basic:cagr",
        inputs=[_input("start", "100"), _input("end", "121")],
        parameters={"years": 2},
        output_value=output,
        # `pure`, which is what `cagr` returns. Stored as "ratio" the row diverges on its
        # unit before the arithmetic is ever compared, and the value check goes untested.
        output_unit="pure",
        code_version="test",
    )


class TestItCostsNothing:
    async def test_the_replay_makes_no_model_call(
        self, db_session: AsyncSession, scene: dict[str, Any], settings: Settings
    ) -> None:
        """Reproduction is a question about the record, not about the world.

        A replay that re-called a model would cost money every time somebody audited a run,
        and would answer a different question each time the model changed.
        """
        before = await db_session.scalar(
            select(AgentRun.id).where(AgentRun.id == scene["agent_run"].id)
        )

        await replay_run(db_session, scene["store"], job_id=scene["job"].id, settings=settings)

        rows = list(
            await db_session.scalars(
                select(AgentRun)
                .join(JobStep, JobStep.id == AgentRun.job_step_id)
                .where(JobStep.job_id == scene["job"].id)
            )
        )
        assert before is not None
        assert len(rows) == 1, "a replay must not record a new exchange"
