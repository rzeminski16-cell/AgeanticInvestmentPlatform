"""The workflow graph: bounded fan-out, drained stops, and a ledger that survives it.

Task 34. Two layers.

The **graph validation** tests are pure: a malformed workflow — duplicate keys, a forward
dependency, an unknown ``stop_after`` — must refuse at construction, because each would
otherwise surface mid-run with money already spent.

The **concurrency** tests run against Postgres with a real session factory and real
commits, because that is the design: parallel nodes each own a session, and a fixture that
handed every node one savepoint-joined session would be testing an arrangement the engine
refuses to run. Concurrency is *observed*, never assumed — a high-water mark of in-flight
nodes, recorded by the nodes themselves — and every stop is checked to have drained: a
sibling in flight when a pause or failure lands still runs to its own recorded outcome.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aer.agents.base import Agent, AgentContext
from aer.calc.basic import ratio
from aer.calc.engine import CalculationContext
from aer.calc.units import Quantity, SourceRef
from aer.core.enums import JobStatus, RequestStatus, UserRole
from aer.db.models import (
    Calculation,
    Job,
    JobCancellation,
    JobStep,
    Prompt,
    ResearchRequest,
    User,
)
from aer.eval.replay import replay_observations_for_job
from aer.services.calculations import persist_context
from aer.workflow.engine import (
    MAX_PARALLEL_NODES,
    BudgetGuard,
    StepContext,
    StepFunction,
    StepPaused,
    StepResult,
    WorkflowDefinitionError,
    WorkflowEngine,
    WorkflowStep,
)
from tests.agent_probes import ProbeAnswer

# A duration long enough that two nodes started together are reliably in flight at once,
# short enough that a suite full of them stays quick.
_BREATH = 0.05


class _PromptProbe(Agent[str, ProbeAnswer]):
    """Reaches the base's find-or-create without a provider or a model call.

    ``_ensure_prompt`` needs a session and nothing else from the context, so the wrapper
    below is honest about that rather than assembling a whole ``AgentContext`` of stubs to
    satisfy fields it never reads.
    """

    role = "evaluation-probe"
    output_schema = ProbeAnswer

    def system_prompt(self, value: str) -> str:  # pragma: no cover -- never invoked
        return value

    def user_message(self, value: str) -> str:  # pragma: no cover -- never invoked
        return value

    async def ensure_prompt_for(self, session: AsyncSession, template: str) -> Prompt:
        context = cast("AgentContext", SimpleNamespace(session=session))
        return await self._ensure_prompt(context, template)


@dataclass(slots=True)
class Tracker:
    """What the nodes themselves observed: who ran, when, and how many at once."""

    current: int = 0
    high_water: int = 0
    started: list[str] = field(default_factory=list)
    finished: list[str] = field(default_factory=list)
    calls: dict[str, int] = field(default_factory=dict)

    async def work(self, key: str, duration: float = _BREATH) -> None:
        self.calls[key] = self.calls.get(key, 0) + 1
        self.current += 1
        self.high_water = max(self.high_water, self.current)
        self.started.append(key)
        await asyncio.sleep(duration)
        self.current -= 1
        self.finished.append(key)


def _step(
    key: str,
    tracker: Tracker | None = None,
    *,
    needs: frozenset[str] | None = None,
    estimate: Decimal | None = None,
    body: Any = None,
) -> WorkflowStep:
    async def run(context: StepContext) -> StepResult:
        if tracker is not None:
            await tracker.work(key)
        if body is not None:
            return await body(context)
        return StepResult(output={"ran": key})

    return WorkflowStep(
        key=key,
        run=run,
        needs=needs,
        estimated_cost_gbp=estimate if estimate is not None else Decimal(0),
    )


FREE: frozenset[str] = frozenset()


class TestTheGraphIsValidatedAtConstruction:
    def test_two_steps_claiming_one_key_are_refused(self) -> None:
        with pytest.raises(WorkflowDefinitionError, match="Two steps claim"):
            WorkflowEngine([_step("a"), _step("a")])

    def test_a_dependency_must_be_declared_earlier(self) -> None:
        # Backwards-pointing dependencies are what make a cycle unrepresentable rather
        # than merely checked for.
        with pytest.raises(WorkflowDefinitionError, match=r"not .*declared earlier"):
            WorkflowEngine([_step("a", needs=frozenset({"b"})), _step("b")])

    def test_a_dependency_on_itself_is_refused(self) -> None:
        with pytest.raises(WorkflowDefinitionError, match=r"not .*declared earlier"):
            WorkflowEngine([_step("a", needs=frozenset({"a"}))])

    def test_the_chain_default_follows_declaration_order(self) -> None:
        # Every workflow the first three phases wrote, unchanged: no `needs` means "after
        # the step declared before me".
        engine = WorkflowEngine([_step("a"), _step("b"), _step("c")])

        assert engine._needs == {"a": frozenset(), "b": {"a"}, "c": {"b"}}

    def test_an_explicit_empty_needs_is_a_free_node(self) -> None:
        engine = WorkflowEngine([_step("a"), _step("b", needs=FREE)])

        assert engine._needs["b"] == frozenset()

    @pytest.mark.parametrize("bound", [0, MAX_PARALLEL_NODES + 1])
    def test_the_fan_out_bound_is_not_configuration(self, bound: int) -> None:
        # §2.5: max 7 workers. The ceiling is part of the cost model; a workflow cannot
        # raise it, only lower it for a test.
        with pytest.raises(WorkflowDefinitionError, match="bound is 1 to"):
            WorkflowEngine([_step("a")], max_parallel=bound)

    def test_stop_after_must_name_a_step(self) -> None:
        # Ignoring an unknown key would run the whole workflow — past whatever gate the
        # caller meant to stop at, which is the spending direction of wrong.
        engine = WorkflowEngine([_step("a"), _step("b")])

        with pytest.raises(WorkflowDefinitionError, match="not a step of this workflow"):
            engine._targets("a-typo")

    def test_stop_after_reaches_only_its_ancestors(self) -> None:
        engine = WorkflowEngine(
            [
                _step("a"),
                _step("b", needs=frozenset({"a"})),
                _step("c", needs=frozenset({"a"})),
                _step("d", needs=frozenset({"b", "c"})),
            ]
        )

        assert engine._targets("b") == {"a", "b"}
        assert engine._targets("d") == {"a", "b", "c", "d"}


# ==========================================================================================
# Against Postgres: real sessions, real commits, observed concurrency
# ==========================================================================================


@pytest.fixture
async def clean_slate(db_engine: Any) -> None:
    """Empty everything these tests write, before each one.

    The engine commits for real, so its writes outlive the test that made them. Truncated
    at setup rather than teardown: it is what the *next* test needs, and doing it here
    cannot contend with a transaction a finished test still holds open.
    """
    async with db_engine.begin() as connection:
        await connection.execute(text("SET LOCAL statement_timeout = '5s'"))
        await connection.execute(
            text(
                "TRUNCATE calculations, costs, job_steps, job_cancellations, jobs, "
                "research_requests, users RESTART IDENTITY CASCADE"
            )
        )


@pytest.fixture
async def scene(clean_slate: None, db_engine: Any) -> dict[str, Any]:
    """A committed job for the graph to run against, and the factory its nodes share."""
    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    async with factory() as session:
        user = User(email="dag@example.invalid", display_name="DAG", role=UserRole.OWNER)
        session.add(user)
        await session.flush()

        request = ResearchRequest(
            user_id=user.id,
            company_name="Contoso Corporation",
            ticker="CTSO",
            exchange="NASDAQ",
            as_of_date=date(2023, 1, 1),
            base_currency="USD",
            investment_horizon_months=12,
            max_cost_gbp="2.00",
            portfolio_context={},
            status=RequestStatus.APPROVED,
        )
        session.add(request)
        await session.flush()

        job = Job(
            request_id=request.id,
            workflow_version="dag-test-1",
            code_version="a1b2c3d4",
            status=JobStatus.RUNNING,
            # Real runs stamp this in `runs.execute` before the engine starts; the
            # `finished_implies_started` constraint holds cancellation to it.
            started_at=datetime.now(UTC),
        )
        session.add(job)
        await session.commit()
        return {"factory": factory, "job_id": job.id}


async def _run(
    scene: dict[str, Any],
    engine: WorkflowEngine,
    *,
    services: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    factory = scene["factory"]
    async with factory() as control:
        job = await control.get(Job, scene["job_id"])
        assert job is not None
        return await engine.run(
            control, job=job, services={"session_factory": factory, **(services or {})}
        )


async def _rows(scene: dict[str, Any]) -> dict[str, JobStep]:
    async with scene["factory"]() as session:
        found = await session.scalars(select(JobStep).where(JobStep.job_id == scene["job_id"]))
        return {row.step_key: row for row in found}


async def _job_status(scene: dict[str, Any]) -> JobStatus:
    async with scene["factory"]() as session:
        job = await session.get(Job, scene["job_id"])
        assert job is not None
        return job.status


@pytest.mark.integration
class TestIndependentNodesRunTogether:
    async def test_the_diamond_overlaps_in_the_middle(self, scene: dict[str, Any]) -> None:
        tracker = Tracker()

        async def join(context: StepContext) -> StepResult:
            # The join proves the graph as well as the schedule: it reads both branches.
            return StepResult(
                output={
                    "b": context.output_of("b")["ran"],
                    "c": context.output_of("c")["ran"],
                }
            )

        engine = WorkflowEngine(
            [
                _step("a", tracker),
                _step("b", tracker, needs=frozenset({"a"})),
                _step("c", tracker, needs=frozenset({"a"})),
                _step("d", tracker, needs=frozenset({"b", "c"}), body=join),
            ]
        )
        outputs = await _run(scene, engine)

        # Observed, not assumed: both middle nodes were in flight at once.
        assert tracker.high_water >= 2
        assert outputs["d"] == {"b": "b", "c": "c"}
        # The join started only after both branches finished.
        assert tracker.started.index("d") > tracker.finished.index("b")
        assert tracker.started.index("d") > tracker.finished.index("c")

    async def test_a_node_is_visible_as_running_while_it_runs(self, scene: dict[str, Any]) -> None:
        """The step row is committed before the step body, not after it.

        Flushing alone kept it inside the engine's transaction, so for the whole of a step
        -- minutes, for a model call -- nothing else could see it had begun. The console
        showed the job RUNNING in its header and every step below it QUEUED, and the
        elapsed clock that exists to prove a long step is alive never appeared.

        Read from a *separate session*, because that is the only way to distinguish
        "committed" from "flushed": the engine's own session would see its uncommitted row
        either way.
        """
        seen: dict[str, Any] = {}

        async def peek(context: StepContext) -> StepResult:
            async with scene["factory"]() as onlooker:
                row = await onlooker.scalar(
                    select(JobStep).where(
                        JobStep.job_id == scene["job_id"], JobStep.step_key == "watched"
                    )
                )
                seen["row"] = row
            return StepResult(output={})

        await _run(scene, WorkflowEngine([WorkflowStep(key="watched", run=peek)]))

        row = seen["row"]
        assert row is not None, "a step in flight was invisible to every other process"
        assert row.status is JobStatus.RUNNING
        assert row.started_at is not None

    async def test_parallel_nodes_sharing_a_role_do_not_collide_on_the_prompt_row(
        self, scene: dict[str, Any]
    ) -> None:
        """The unique violation that killed a live run, reproduced.

        The five research workers share the ``analysis`` role and carry a different topic
        brief each, so five different templates race to create ``(agent.<role>, 1)``. All
        five looked, all five found nothing, all five inserted, and four died on
        ``uq_prompts_key_version``.

        Where it landed made it far worse than a lost row: the violation broke the flush,
        the broken flush made the session refuse to commit, and the commit that refused was
        the one recording the failure — so the run died with nothing written and the console
        showed it running for an hour afterwards. Both halves have their own test; this is
        the cause.
        """

        def writes_a_prompt(template: str) -> StepFunction:
            async def step(context: StepContext) -> StepResult:
                agent = _PromptProbe()
                prompt = await agent.ensure_prompt_for(context.session, template)
                return StepResult(output={"version": prompt.version})

            return step

        briefs = [f"Your topic:\nTopic number {index}.\n" for index in range(5)]
        outputs = await _run(
            scene,
            WorkflowEngine(
                [
                    WorkflowStep(
                        key=f"worker_{index}", run=writes_a_prompt(brief), needs=frozenset()
                    )
                    for index, brief in enumerate(briefs)
                ]
            ),
        )

        assert len(outputs) == 5, "a node died on the prompt row"
        async with scene["factory"]() as session:
            rows = list(
                await session.scalars(
                    select(Prompt).where(Prompt.key == f"agent.{_PromptProbe.role}")
                )
            )

        # One row per distinct template, each recording the text it really used, and no two
        # sharing a version.
        assert len(rows) == 5
        assert len({row.content_hash for row in rows}) == 5
        assert len({row.version for row in rows}) == 5

    async def test_a_step_that_breaks_its_session_is_still_recorded_as_failed(
        self, scene: dict[str, Any]
    ) -> None:
        """The half that turned a failed run into an invisible one.

        A step that dies inside a *flush* leaves its session in pending-rollback, so the
        commit that records the failure raises as well. That second exception escaped the
        node, escaped ``gather`` and killed the queue job — no FAILED step, no FAILED job,
        and a console that went on reporting a run which had been dead for an hour.
        """

        async def poison(context: StepContext) -> StepResult:
            # A valid-looking hash, so the flush breaks on the *unique* constraint the
            # live run hit rather than on the hash's own check.
            digest = "a" * 64
            for _ in range(2):
                context.session.add(
                    Prompt(key="agent.poison", version="1", template="x", content_hash=digest)
                )
            await context.session.flush()
            return StepResult(output={})  # pragma: no cover -- the flush raises

        with pytest.raises(Exception, match="uq_prompts_key_version"):
            await _run(scene, WorkflowEngine([WorkflowStep(key="poisoned", run=poison)]))

        rows = await _rows(scene)
        assert rows["poisoned"].status is JobStatus.FAILED
        assert (rows["poisoned"].error or {})["code"] == "unexpected_error"
        assert await _job_status(scene) is JobStatus.FAILED

    async def test_every_node_left_a_succeeded_row(self, scene: dict[str, Any]) -> None:
        engine = WorkflowEngine(
            [
                _step("a"),
                _step("b", needs=frozenset({"a"})),
                _step("c", needs=frozenset({"a"})),
                _step("d", needs=frozenset({"b", "c"})),
            ]
        )
        await _run(scene, engine)

        rows = await _rows(scene)
        assert {key: row.status for key, row in rows.items()} == {
            "a": JobStatus.SUCCEEDED,
            "b": JobStatus.SUCCEEDED,
            "c": JobStatus.SUCCEEDED,
            "d": JobStatus.SUCCEEDED,
        }
        # Sequence is the declaration index — stable identity whatever the schedule did.
        assert {key: row.sequence for key, row in rows.items()} == {
            "a": 0,
            "b": 1,
            "c": 2,
            "d": 3,
        }

    async def test_readiness_means_every_dependency_not_any(self, scene: dict[str, Any]) -> None:
        # Staggered depths: d needs both the first node and the *third*. A scheduler that
        # treats one satisfied dependency as readiness would start d in the second wave,
        # beside b, with c not yet run — the whole-wave drain of the diamond test cannot
        # see that mistake, which is why this shape exists.
        tracker = Tracker()

        async def join(context: StepContext) -> StepResult:
            return StepResult(output={"c": context.output_of("c")["ran"]})

        engine = WorkflowEngine(
            [
                _step("a", tracker),
                _step("b", tracker, needs=frozenset({"a"})),
                _step("c", tracker, needs=frozenset({"b"})),
                _step("d", tracker, needs=frozenset({"a", "c"}), body=join),
            ]
        )
        outputs = await _run(scene, engine)

        assert outputs["d"] == {"c": "c"}
        assert tracker.started.index("d") > tracker.finished.index("c")

    async def test_the_bound_holds_under_load(self, scene: dict[str, Any]) -> None:
        tracker = Tracker()
        free_nodes = [_step(f"n{i}", tracker, needs=FREE) for i in range(8)]
        engine = WorkflowEngine(free_nodes, max_parallel=3)

        await _run(scene, engine)

        assert tracker.high_water <= 3
        assert tracker.high_water >= 2
        assert len(tracker.finished) == 8


@pytest.mark.integration
class TestAFailedNodeFailsOnlyItsDependants:
    async def test_the_independent_branch_completes_and_keeps_its_work(
        self, scene: dict[str, Any]
    ) -> None:
        tracker = Tracker()

        async def explode(context: StepContext) -> StepResult:
            message = "deliberate"
            raise RuntimeError(message)

        engine = WorkflowEngine(
            [
                _step("a", tracker),
                _step("b", tracker, needs=frozenset({"a"}), body=explode),
                _step("c", tracker, needs=frozenset({"a"})),
                _step("d", tracker, needs=frozenset({"b"})),
                _step("e", tracker, needs=frozenset({"c"})),
            ]
        )

        with pytest.raises(RuntimeError, match="deliberate"):
            await _run(scene, engine)

        rows = await _rows(scene)
        assert rows["b"].status is JobStatus.FAILED
        # The independent branch ran to completion after the failure landed.
        assert rows["c"].status is JobStatus.SUCCEEDED
        assert rows["e"].status is JobStatus.SUCCEEDED
        # The dependant of the failure never started: no row at all.
        assert "d" not in rows
        assert await _job_status(scene) is JobStatus.FAILED


@pytest.mark.integration
class TestAPauseDrainsTheWave:
    async def test_the_sibling_in_flight_finishes_before_the_pause_lands(
        self, scene: dict[str, Any]
    ) -> None:
        tracker = Tracker()
        approvals: dict[str, bool] = {"granted": False}

        async def gate(context: StepContext) -> StepResult:
            if not approvals["granted"]:
                message = "A person has to look at this."
                raise StepPaused(message, gate="the-gate")
            return StepResult(output={"ran": "gate_b"})

        engine = WorkflowEngine(
            [
                _step("a", tracker),
                WorkflowStep(key="gate_b", run=gate, needs=frozenset({"a"})),
                _step("c", tracker, needs=frozenset({"a"})),
                # Ready the moment c completes, gate or no gate — the node that catches a
                # pause which stops the run's status but not its scheduling.
                _step("f", tracker, needs=frozenset({"c"})),
                _step("d", tracker, needs=frozenset({"gate_b", "c"})),
            ]
        )

        outputs = await _run(scene, engine)

        rows = await _rows(scene)
        # The sibling drained to success; the run then paused; nothing further started —
        # not the joint dependant, and not the node the drained sibling had unblocked.
        assert rows["c"].status is JobStatus.SUCCEEDED
        assert rows["gate_b"].status is JobStatus.AWAITING_APPROVAL
        assert "d" not in rows
        assert "f" not in rows
        assert "c" in outputs
        assert await _job_status(scene) is JobStatus.AWAITING_APPROVAL

        # The same call again, once approved, finishes the run — re-executing only what
        # never succeeded.
        approvals["granted"] = True
        async with scene["factory"]() as session:
            job = await session.get(Job, scene["job_id"])
            assert job is not None
            job.status = JobStatus.RUNNING
            await session.commit()

        outputs = await _run(scene, engine)

        rows = await _rows(scene)
        assert rows["d"].status is JobStatus.SUCCEEDED
        assert tracker.calls == {"a": 1, "c": 1, "f": 1, "d": 1}
        assert outputs["gate_b"] == {"ran": "gate_b"}


@pytest.mark.integration
class TestAPauseStopsTheSerialPathToo:
    async def test_without_a_factory_a_pause_still_ends_the_round(
        self, scene: dict[str, Any]
    ) -> None:
        # No session factory: a parallel-shaped graph degrades to one node at a time on
        # the caller's session. The declared-first gate pauses — and its *independent*
        # sibling, ready and next in line, must not run into a run that has stopped.
        tracker = Tracker()

        async def gate(context: StepContext) -> StepResult:
            message = "A person has to look at this."
            raise StepPaused(message, gate="the-gate")

        engine = WorkflowEngine(
            [
                _step("a", tracker),
                WorkflowStep(key="gate_b", run=gate, needs=frozenset({"a"})),
                _step("c", tracker, needs=frozenset({"a"})),
            ]
        )
        factory = scene["factory"]
        async with factory() as control:
            job = await control.get(Job, scene["job_id"])
            assert job is not None
            await engine.run(control, job=job, services={})

        rows = await _rows(scene)
        assert rows["gate_b"].status is JobStatus.AWAITING_APPROVAL
        assert "c" not in rows
        assert tracker.calls.get("c") is None
        assert await _job_status(scene) is JobStatus.AWAITING_APPROVAL


@pytest.mark.integration
class TestTheBudgetSeesInFlightSiblings:
    async def test_two_siblings_jointly_over_the_cap_do_not_both_start(
        self, scene: dict[str, Any]
    ) -> None:
        tracker = Tracker()
        engine = WorkflowEngine(
            [
                _step("a", tracker),
                _step("b", tracker, needs=frozenset({"a"}), estimate=Decimal("0.03")),
                _step("c", tracker, needs=frozenset({"a"}), estimate=Decimal("0.03")),
                # Cheap enough to fit even after the refusal — which is the trap: a run
                # that has decided to pause must not keep starting affordable siblings.
                _step("e", tracker, needs=frozenset({"a"}), estimate=Decimal("0.01")),
                _step("d", tracker, needs=frozenset({"b", "c"})),
            ],
            budget=BudgetGuard(per_run_cap_gbp=Decimal("0.05"), monthly_cap_gbp=Decimal(100)),
        )

        await _run(scene, engine)

        rows = await _rows(scene)
        # Individually each estimate fits the cap; jointly they do not. One ran, the
        # other was refused before it spent anything, and the run paused for a decision.
        assert rows["b"].status is JobStatus.SUCCEEDED
        assert rows["c"].status is JobStatus.BUDGET_EXCEEDED
        assert "d" not in rows
        assert tracker.calls.get("c") is None
        assert tracker.calls.get("e") is None
        assert await _job_status(scene) is JobStatus.BUDGET_EXCEEDED


@pytest.mark.integration
class TestCancellationBetweenWaves:
    async def test_a_cancellation_landed_mid_run_stops_the_next_wave(
        self, scene: dict[str, Any]
    ) -> None:
        tracker = Tracker()

        async def cancel_from_within(context: StepContext) -> StepResult:
            # Another process asking the run to stop, simulated from the one place this
            # test can reach mid-run. The node's own session commits it, exactly as the
            # web process would.
            context.session.add(JobCancellation(job_id=context.job.id, reason="test"))
            return StepResult(output={"ran": "b"})

        engine = WorkflowEngine(
            [
                _step("a", tracker),
                WorkflowStep(key="b", run=cancel_from_within, needs=frozenset({"a"})),
                _step("c", tracker, needs=frozenset({"a"})),
                _step("d", tracker, needs=frozenset({"b", "c"})),
            ]
        )

        await _run(scene, engine)

        rows = await _rows(scene)
        assert rows["b"].status is JobStatus.SUCCEEDED
        assert rows["c"].status is JobStatus.SUCCEEDED
        assert "d" not in rows
        assert tracker.calls.get("d") is None
        assert await _job_status(scene) is JobStatus.CANCELLED


@pytest.mark.integration
class TestResumeAfterACrash:
    async def test_only_the_incomplete_node_re_executes(self, scene: dict[str, Any]) -> None:
        tracker = Tracker()
        crashes: dict[str, bool] = {"armed": True}

        async def flaky(context: StepContext) -> StepResult:
            if crashes["armed"]:
                message = "the worker died here"
                raise RuntimeError(message)
            return StepResult(output={"ran": "d"})

        engine = WorkflowEngine(
            [
                _step("a", tracker),
                _step("b", tracker, needs=frozenset({"a"})),
                _step("c", tracker, needs=frozenset({"a"})),
                WorkflowStep(key="d", run=flaky, needs=frozenset({"b", "c"})),
            ]
        )

        with pytest.raises(RuntimeError, match="died here"):
            await _run(scene, engine)

        crashes["armed"] = False
        async with scene["factory"]() as session:
            job = await session.get(Job, scene["job_id"])
            assert job is not None
            job.status = JobStatus.RUNNING
            await session.commit()

        outputs = await _run(scene, engine)

        # The finished branches were skipped from their stored rows; only the crash
        # point ran again.
        assert tracker.calls == {"a": 1, "b": 1, "c": 1}
        assert outputs["d"] == {"ran": "d"}
        rows = await _rows(scene)
        assert rows["d"].attempt == 1


@pytest.mark.integration
class TestTheLedgerUnderConcurrency:
    async def test_parallel_nodes_write_distinct_sequence_ranges(
        self, scene: dict[str, Any]
    ) -> None:
        barrier = asyncio.Barrier(2)

        def calculate(which: str) -> Any:
            async def body(context: StepContext) -> StepResult:
                # Both nodes provably inside their step at once, so the two
                # persist_context calls contend rather than happening to serialise.
                await barrier.wait()
                calc = CalculationContext(code_version="dag-test")
                source = SourceRef.financial_fact(str(uuid.uuid4()), label=which)
                first = ratio(
                    calc,
                    numerator=Quantity.of(Decimal(250), "USD", source=source),
                    denominator=Quantity.of(Decimal(1000), "USD", source=source),
                )
                ratio(
                    calc,
                    numerator=first,
                    denominator=Quantity.of(Decimal(2), "pure", source=source),
                )
                await persist_context(context.session, calc, job_id=context.job.id)
                return StepResult(output={"ran": which})

            return body

        engine = WorkflowEngine(
            [
                _step("a"),
                WorkflowStep(key="b", run=calculate("b"), needs=frozenset({"a"})),
                WorkflowStep(key="c", run=calculate("c"), needs=frozenset({"a"})),
            ]
        )
        await _run(scene, engine)

        async with scene["factory"]() as session:
            rows = list(
                await session.scalars(
                    select(Calculation).where(Calculation.job_id == scene["job_id"])
                )
            )
            observations = await replay_observations_for_job(session, scene["job_id"])

        # Four rows, four distinct ledger positions — not two overlapping 0..1 ranges.
        assert sorted(row.sequence for row in rows) == [0, 1, 2, 3]
        # Which is exactly what keeps a replay label naming one row of one run.
        labels = [observation.name for observation in observations]
        assert len(labels) == len(set(labels))
        assert all(observation.error is None for observation in observations)
