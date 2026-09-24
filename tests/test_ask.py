"""Ask, in three tiers (F6, ADR 0130).

The resolver is pure and holds the testing strategy's two properties: a fixed corpus of
questions resolves to the tiers it names, and no edit that removes something the record
holds ever lowers a tier. Tier 1 is struck on the assumption scene's valuation: the base
case as held reproduces the report's own figure, the changed case moves the way the change
says, and the changed input's lineage names the question. Tier 2 runs the fake provider
through the real agent base, and every check in ``_judge`` is exercised: a citation outside
the pack, a stray numeral, a reader that says the material does not answer. The adversarial
corpus — questions outside the record — produces no answer and no model call, only a
price. The pages render each state.
"""

from __future__ import annotations

import re
import time
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aer.agents.ask_reader import AnswerParagraph, AskAnswer
from aer.agents.worker import ToolRequest, WorkerLead, WorkerReport, WorkerTurn
from aer.calc.units import Quantity, SourceRef, Unit
from aer.config import Settings
from aer.core.ask import (
    PARAMETERS,
    Change,
    HeldRecord,
    Tier,
    empty_record,
    resolve,
    subject_tokens,
)
from aer.core.enums import ExtractionKind, JobStatus, Provider, SourceTier, UserRole
from aer.core.hashing import canonical_json, sha256_hex
from aer.db.models import (
    Artefact,
    Calculation,
    Company,
    Cost,
    Extraction,
    Job,
    JobStep,
    Report,
    SourceDocument,
    User,
)
from aer.errors import ConflictError, ValidationError
from aer.providers.fake import FakeProvider
from aer.providers.router import Router
from aer.services import ask as ask_service
from aer.services.assumptions import confirm, propose
from aer.services.calculations import lineage
from aer.services.valuation_run import value_the_business
from aer.services.valuation_view import valuation_view
from aer.storage.local import LocalArtefactStore
from tests.api_fixtures import build_app, client_for
from tests.assumption_fixtures import analysed, seed_years
from tests.report_fixtures import make_current
from tests.request_fixtures import research_request
from tests.schema_guard import refuse_unanswerable_schema
from tests.test_composed_view import _CONFIRMED, _SHARES, _YEARS, MANDATE, _price
from tests.test_research_workers import _RecordingFetcher

pytestmark = pytest.mark.usefixtures("db_session")

# -- The resolver, pure ------------------------------------------------------------------------

RECORD = HeldRecord(
    subject_names=subject_tokens("Microsoft Corporation", "MSFT"),
    variable_inputs=frozenset(
        {"wacc", "terminal_growth", "exit_multiple", "revenue_growth", "ebit_margin", "tax_rate"}
    ),
    document_titles=("microsoft 10-k 2024", "form 8-k exhibit 99.1 press release"),
    years=frozenset({2022, 2023, 2024}),
    has_quarterly=False,
    other_names=subject_tokens("Oracle Corporation"),
    vocabulary=frozenset({"azure", "intelligent", "cloud", "windows"}),
)

# The fixed question corpus with its expected tiers (11-testing-strategy, F6).
CORPUS: tuple[tuple[str, Tier], ...] = (
    ("What if the discount rate were half a point higher?", Tier.RECOMPUTE),
    ("What if the discount rate were 50 bps lower?", Tier.RECOMPUTE),
    ("What if growth were 3% rather than 5%?", Tier.RECOMPUTE),
    ("Value per share at a 10x exit multiple?", Tier.RECOMPUTE),
    ("What if the terminal growth rate were 2%?", Tier.RECOMPUTE),
    ("What if the tax rate were two points higher?", Tier.RECOMPUTE),
    ("What if the discount rate were higher?", Tier.RE_READ),
    ("What does the 10-K say about lease obligations?", Tier.RE_READ),
    ("What did Microsoft say about Azure in 2024?", Tier.RE_READ),
    ("Is the operating margin sustainable?", Tier.RE_READ),
    ("How does Oracle compare on margins?", Tier.RE_READ),
    ("Has anything changed at their main competitor?", Tier.RESEARCH),
    ("How does Amazon compare on margins?", Tier.RESEARCH),
    ("What was revenue in 2021?", Tier.RESEARCH),
    ("What did the Q3 transcript say about guidance?", Tier.RESEARCH),
    ("What does the proxy say about pay?", Tier.RESEARCH),
    ("What is the share price today?", Tier.RESEARCH),
    ("Any news since the last results?", Tier.RESEARCH),
    ("", Tier.RESEARCH),
    # The other tier-1 shape (ADR 0122 §2): answered from the theses and the book.
    ("Which of my positions rest on the same belief?", Tier.RECOMPUTE),
    ("Which holdings depend on the same premise?", Tier.RECOMPUTE),
    ("Where else is this premise load-bearing?", Tier.RECOMPUTE),
)


class TestTheResolver:
    @pytest.mark.parametrize(
        ("question", "expected"), CORPUS, ids=[q[:40] or "empty" for q, _ in CORPUS]
    )
    def test_the_corpus_resolves_to_its_tier(self, question: str, expected: Tier) -> None:
        resolved = resolve(question, RECORD)
        assert resolved.tier is expected, resolved.rationale
        assert resolved.rationale

    def test_a_change_carries_its_value_and_direction(self) -> None:
        half_point = resolve("What if the discount rate were half a point higher?", RECORD)
        absolute = resolve("What if growth were 3% rather than 5%?", RECORD)
        multiple = resolve("Value per share at a 10x exit multiple?", RECORD)
        lower = resolve("What if the discount rate were 50 bps lower?", RECORD)

        assert half_point.change == Change(
            "wacc", "relative", Decimal("0.005"), "0.5 percentage points"
        )
        assert absolute.change == Change("revenue_growth", "absolute", Decimal("0.03"), "3%")
        assert multiple.change == Change("exit_multiple", "absolute", Decimal(10), "10x")
        assert lower.change is not None
        assert lower.change.value == Decimal("-0.005")

    def test_a_parameter_without_a_value_is_not_tier_one(self) -> None:
        resolved = resolve("What if the discount rate were higher?", RECORD)
        assert resolved.tier is Tier.RE_READ
        assert resolved.change is None

    def test_an_input_the_run_does_not_hold_is_not_tier_one(self) -> None:
        without = HeldRecord(
            subject_names=RECORD.subject_names,
            variable_inputs=frozenset(),
            document_titles=RECORD.document_titles,
            years=RECORD.years,
            has_quarterly=False,
        )
        resolved = resolve("What if the discount rate were half a point higher?", without)
        assert resolved.tier is not Tier.RECOMPUTE

    def test_an_empty_record_sends_everything_it_cannot_recompute_to_research(self) -> None:
        resolved = resolve("What does the 10-K say about leases?", empty_record())
        assert resolved.tier is Tier.RESEARCH
        assert "no document at all" in resolved.rationale

    def test_every_parameter_speaks_its_first_phrase_in_lower_case(self) -> None:
        """The words a rationale uses are the first phrase, and matching is case-blind."""
        for parameter in PARAMETERS:
            assert parameter.words == parameter.phrases[0]
            assert all(phrase == phrase.lower() for phrase in parameter.phrases)
            assert parameter.measure in {"fraction", "multiple"}

    @settings(max_examples=150, deadline=None)
    @given(
        st.lists(st.sampled_from(sorted(RECORD.variable_inputs)), unique=True),
        st.lists(st.sampled_from(RECORD.document_titles), unique=True),
        st.lists(st.sampled_from(sorted(RECORD.years)), unique=True),
        st.booleans(),
        st.lists(st.sampled_from(sorted(RECORD.other_names)), unique=True),
        st.lists(st.sampled_from(sorted(RECORD.vocabulary)), unique=True),
        st.sampled_from([question for question, _ in CORPUS if question]),
    )
    def test_removing_anything_the_record_holds_never_lowers_the_tier(
        self,
        inputs: list[str],
        titles: list[str],
        years: list[int],
        quarterly: bool,
        others: list[str],
        vocabulary: list[str],
        question: str,
    ) -> None:
        """The one-directional error, as a property: a smaller record resolves no lower."""
        smaller = HeldRecord(
            subject_names=RECORD.subject_names,
            variable_inputs=frozenset(inputs),
            document_titles=tuple(titles),
            years=frozenset(years),
            has_quarterly=quarterly and RECORD.has_quarterly,
            other_names=frozenset(others),
            vocabulary=frozenset(vocabulary),
        )
        assert resolve(question, smaller).tier >= resolve(question, RECORD).tier


# -- The scene --------------------------------------------------------------------------------


def _settings(tmp_path: Path, **overrides: Any) -> Settings:
    return Settings(
        http_user_agent="Test test@example.invalid",
        artefact_root=tmp_path / "artefacts",
        **overrides,
    )


def _provider(answer: AskAnswer | None = None) -> FakeProvider:
    return FakeProvider(
        {"AskAnswer": answer} if answer is not None else {},
        inspect_schema=refuse_unanswerable_schema,
    )


async def _valued(scene: dict[str, Any], *, price: str = "50") -> Report:
    """The assumption scene, valued at a market price and frozen into a current report."""
    session: AsyncSession = scene["session"]
    scene["request"].company_id = scene["company"].id
    scene["document"].company_id = scene["company"].id
    scene["document"].title = "Contoso Corporation 10-K 2024"
    await session.flush()
    await seed_years(scene, _YEARS)
    actor = User(email="operator@example.invalid", display_name="O", role=UserRole.OWNER)
    session.add(actor)
    await session.flush()
    for name, value in _CONFIRMED.items():
        assumption = await propose(
            session,
            request_id=scene["request"].id,
            name=name,
            value=Decimal(value),
            unit="pure",
            justification=f"Scene value for {name}.",
            proposed_by="test",
        )
        await confirm(session, assumption=assumption, actor=actor)
    capitalisation = Quantity.of(
        Decimal(price) * Decimal(_SHARES["diluted_shares_outstanding"]),
        Unit.currency("USD"),
        source=SourceRef.security("a-listing", label="market capitalisation"),
    )
    await value_the_business(
        session,
        request=scene["request"],
        job_id=scene["job"].id,
        analysis=await analysed(scene),
        mandate=MANDATE,
        years=5,
        market_capitalisation=capitalisation,
        price_per_share=_price(price),
    )
    scene["job"].status = JobStatus.SUCCEEDED
    # What the price step records on a real run, so the recompute reads the same
    # capitalisation the report's cost of capital was weighed on.
    session.add(
        JobStep(
            job_id=scene["job"].id,
            step_key="acquire_prices",
            sequence=0,
            status=JobStatus.SUCCEEDED,
            attempt=0,
            idempotency_key=f"{scene['job'].id}:acquire_prices",
            input_hash="a" * 64,
            output_ref={
                "market_capitalisation": {
                    "value": str(capitalisation.value),
                    "source_id": "a-listing",
                    "source_kind": "fact",
                },
                "price_per_share": {
                    "value": price,
                    "source_id": "a-listing",
                    "source_kind": "fact",
                },
            },
        )
    )
    await session.flush()
    content: dict[str, Any] = {"sections": []}
    report = Report(
        job_id=scene["job"].id,
        request_id=scene["request"].id,
        company_id=scene["company"].id,
        as_of_date=scene["request"].work_order.as_of_date,
        approved_by=scene["request"].work_order.user_id,
        approved_at=datetime.now(UTC),
        content=content,
        content_hash=sha256_hex(canonical_json(content)),
    )
    session.add(report)
    await session.flush()
    return await make_current(session, report)


async def _excerpt(scene: dict[str, Any], text: str) -> Extraction:
    locator = {"start": 0, "length": len(text)}
    row = Extraction(
        source_document_id=scene["document"].id,
        kind=ExtractionKind.TEXT,
        extractor="test",
        extractor_version="1",
        locator=locator,
        locator_hash=sha256_hex(canonical_json(locator) + text[:8]),
        excerpt=text,
        content_hash="e" * 64,
    )
    scene["session"].add(row)
    await scene["session"].flush()
    return row


async def _owner(scene: dict[str, Any]) -> User:
    user = await scene["session"].get(User, scene["request"].work_order.user_id)
    assert user is not None
    return user


async def _ask(
    scene: dict[str, Any],
    tmp_path: Path,
    text: str,
    *,
    provider: FakeProvider | None = None,
    **overrides: Any,
) -> Any:
    settings = _settings(tmp_path, **overrides)
    return await ask_service.ask(
        scene["session"],
        settings=settings,
        provider=provider if provider is not None else _provider(),
        router=Router(settings),
        store=LocalArtefactStore(settings.artefact_root, max_bytes=settings.max_artefact_bytes),
        user=await _owner(scene),
        company=scene["company"],
        text=text,
    )


async def _names(session: AsyncSession, job_id: uuid.UUID) -> dict[str, int]:
    counted = await session.execute(
        select(Calculation.name, func.count())
        .where(Calculation.job_id == job_id)
        .group_by(Calculation.name)
    )
    return dict(counted.all())


LEASES = (
    "The company's lease obligations total $1,200 over the next five years, of which $300 "
    "falls due within twelve months. The leases are for office and data-centre space."
)


# -- The record, as read from the store ---------------------------------------------------------


class TestTheRecord:
    async def test_it_reads_the_subject_the_years_and_the_documents(
        self, scene: dict[str, Any]
    ) -> None:
        await _valued(scene)
        record = await ask_service.held_record(
            scene["session"], user=await _owner(scene), company=scene["company"]
        )

        assert {"contoso", "ctso"} <= record.subject_names
        assert record.years >= {2022, 2023, 2024}
        assert not record.has_quarterly
        assert any("10-k" in title for title in record.document_titles)
        assert "wacc" in record.variable_inputs
        assert {"revenue_growth", "terminal_growth", "exit_multiple"} <= record.variable_inputs

    async def test_a_company_with_no_valuation_has_nothing_to_recompute(
        self, scene: dict[str, Any]
    ) -> None:
        scene["request"].company_id = scene["company"].id
        await scene["session"].flush()
        record = await ask_service.held_record(
            scene["session"], user=await _owner(scene), company=scene["company"]
        )
        assert record.variable_inputs == frozenset()
        resolved = resolve("What if the discount rate were half a point higher?", record)
        assert resolved.tier is not Tier.RECOMPUTE

    async def test_the_record_is_the_accounts_own(self, scene: dict[str, Any]) -> None:
        """ADR 0120: another account's runs are not this account's record."""
        await _valued(scene)
        stranger = User(email="stranger@example.invalid", display_name="S", role=UserRole.OWNER)
        scene["session"].add(stranger)
        await scene["session"].flush()

        assert await ask_service.companies_with_a_record(scene["session"], user=stranger) == []
        held = await ask_service.companies_with_a_record(scene["session"], user=await _owner(scene))
        assert [company.id for company in held] == [scene["company"].id]


# -- Tier 1 -----------------------------------------------------------------------------------


class TestRecompute:
    async def test_it_answers_in_figures_for_nothing_inside_five_seconds(
        self, scene: dict[str, Any], tmp_path: Path
    ) -> None:
        report = await _valued(scene)
        started = time.perf_counter()
        question = await _ask(
            scene, tmp_path, "What if the discount rate were half a point higher?"
        )
        elapsed = time.perf_counter() - started

        assert question.tier == 1
        assert question.is_answered
        assert question.actual_cost_gbp == 0
        assert question.report_id == report.id
        assert elapsed < 5.0, f"a tier-1 answer took {elapsed:.1f}s"
        content = question.content
        assert content["kind"] == "recompute"
        assert content["change"]["parameter"] == "wacc"
        moved = Decimal(content["change"]["to"]) - Decimal(content["change"]["from"])
        assert abs(moved - Decimal("0.005")) < Decimal("1e-12")
        assert len(content["figures"]) == 6
        assert all(row["calculation_id"] for row in content["figures"][:4])
        assert "half a point" not in question.answer
        assert "value per share" in question.answer

    async def test_the_base_case_as_held_reproduces_the_reports_own_figure(
        self, scene: dict[str, Any], tmp_path: Path
    ) -> None:
        report = await _valued(scene)
        question = await _ask(
            scene, tmp_path, "What if the discount rate were half a point higher?"
        )
        run = await scene["session"].get(Job, report.job_id)
        assert run is not None
        printed = await valuation_view(scene["session"], run)
        assert printed.gordon.value_per_share is not None

        by_label = {row["label"]: row for row in question.content["figures"]}
        held = next(
            row for label, row in by_label.items() if "Gordon growth, with the inputs" in label
        )
        asked = next(
            row for label, row in by_label.items() if "Gordon growth, with the discount" in label
        )

        assert Decimal(held["value"]).quantize(Decimal("1e-6")) == Decimal(
            printed.gordon.value_per_share.value
        ).quantize(Decimal("1e-6"))
        # A higher discount rate is a lower value: the direction the change says.
        assert Decimal(asked["value"]) < Decimal(held["value"])

    async def test_every_figure_is_on_the_questions_own_ledger(
        self, scene: dict[str, Any], tmp_path: Path
    ) -> None:
        await _valued(scene)
        question = await _ask(
            scene, tmp_path, "What if the discount rate were half a point higher?"
        )
        assert question.job_id is not None
        names = await _names(scene["session"], question.job_id)
        assert names["value_per_share"] == 4  # two cases, two terminal methods
        for row in question.content["figures"][:4]:
            calculation = await scene["session"].get(Calculation, uuid.UUID(row["calculation_id"]))
            assert calculation is not None
            assert calculation.job_id == question.job_id
        costs = await scene["session"].scalar(
            select(func.count()).select_from(Cost).where(Cost.job_id == question.job_id)
        )
        assert costs == 0

    async def test_the_changed_input_traces_to_the_question(
        self, scene: dict[str, Any], tmp_path: Path
    ) -> None:
        await _valued(scene)
        question = await _ask(
            scene, tmp_path, "What if the discount rate were half a point higher?"
        )
        asked = next(
            row
            for row in question.content["figures"]
            if "with the discount rate at" in row["label"]
        )
        tree = await lineage(scene["session"], uuid.UUID(asked["calculation_id"]))

        found: list[Any] = []
        stack = [tree]
        while stack:
            node = stack.pop()
            if node.detail.get("table") == "questions":
                found.append(node)
            stack.extend(node.inputs)
        assert found, "the changed discount rate did not trace to the question"
        assert found[0].kind == "assumption"
        assert found[0].detail["question_id"] == str(question.id)
        assert found[0].value == Decimal(question.content["change"]["to"])

    async def test_a_driver_change_moves_every_forecast_year(
        self, scene: dict[str, Any], tmp_path: Path
    ) -> None:
        await _valued(scene)
        question = await _ask(scene, tmp_path, "What if revenue growth were 8%?")

        assert question.tier == 1
        assert question.is_answered
        assert question.content["change"] == {
            "parameter": "revenue_growth",
            "words": "revenue growth",
            "kind": "absolute",
            "stated": "8%",
            "from": "0.05",
            "to": "0.08",
        }
        assert "in every forecast year" in question.answer
        by_label = {row["label"]: row for row in question.content["figures"]}
        held = next(
            row for label, row in by_label.items() if "Gordon growth, with the inputs" in label
        )
        asked = next(
            row for label, row in by_label.items() if "Gordon growth, with the revenue" in label
        )
        assert Decimal(asked["value"]) > Decimal(held["value"])


# -- Tier 2 -----------------------------------------------------------------------------------


SAID = "The 10-K puts lease obligations at $1,200 over five years, with $300 due within a year."


def _answer(
    *cites: str,
    text: str = SAID,
    answered: bool = True,
    not_in_record: list[str] | None = None,
) -> AskAnswer:
    return AskAnswer(
        answered=answered,
        paragraphs=[AnswerParagraph(text=text, cites=list(cites))],
        not_in_record=not_in_record or [],
    )


class TestReRead:
    async def test_it_answers_from_the_excerpt_it_was_dealt(
        self, scene: dict[str, Any], tmp_path: Path
    ) -> None:
        await _valued(scene)
        excerpt = await _excerpt(scene, LEASES)
        provider = _provider(_answer(str(excerpt.id)))
        question = await _ask(
            scene, tmp_path, "What does the 10-K say about lease obligations?", provider=provider
        )

        assert question.tier == 2
        assert question.is_answered
        assert provider.call_count == 1
        assert question.actual_cost_gbp is not None
        assert question.actual_cost_gbp > 0
        content = question.content
        assert content["kind"] == "re_read"
        assert content["paragraphs"][0]["notes"] == [1]
        assert content["notes"][0] == {
            "number": "1",
            "id": str(excerpt.id),
            "kind": "excerpt",
            "label": content["notes"][0]["label"],
        }
        assert content["dealt"]["documents"] == 1
        assert content["dealt"]["excerpts"] == 1
        assert content["dealt"]["calculations"] > 0

    async def test_the_reader_sees_the_record_and_nothing_else(
        self, scene: dict[str, Any], tmp_path: Path
    ) -> None:
        """Invariant 8: the excerpt reaches the model wrapped as data, labelled by its id."""
        await _valued(scene)
        excerpt = await _excerpt(scene, LEASES)
        provider = _provider(_answer(str(excerpt.id)))
        await _ask(
            scene, tmp_path, "What does the 10-K say about lease obligations?", provider=provider
        )

        [call] = provider.calls
        turn = call["messages"][0]
        assert f"extraction {excerpt.id}" in turn["content"]
        assert "<untrusted_source" in turn["content"]
        assert LEASES in turn["content"]
        assert str(excerpt.id) in (turn["cache_prefix"] or "")
        assert "answer only from the material" in call["system"].lower()

    async def test_a_citation_outside_the_pack_is_dropped_and_none_left_is_a_refusal(
        self, scene: dict[str, Any], tmp_path: Path
    ) -> None:
        await _valued(scene)
        await _excerpt(scene, LEASES)
        provider = _provider(_answer(str(uuid.uuid4())))
        question = await _ask(
            scene, tmp_path, "What does the 10-K say about lease obligations?", provider=provider
        )

        assert provider.call_count == 1
        assert question.tier == 3
        assert not question.is_answered
        assert question.answer is None
        assert question.content["kind"] == "research"
        assert "no paragraph rested" in question.content["discarded"]
        assert question.tier_rationale.startswith("That is not in this record")
        assert question.estimated_cost_gbp is not None
        assert question.estimated_cost_gbp > 0
        assert question.actual_cost_gbp is not None
        assert question.actual_cost_gbp > 0

    async def test_a_figure_the_record_does_not_hold_discards_the_answer(
        self, scene: dict[str, Any], tmp_path: Path
    ) -> None:
        await _valued(scene)
        excerpt = await _excerpt(scene, LEASES)
        provider = _provider(
            _answer(str(excerpt.id), text="Lease obligations are $9,999 over five years.")
        )
        question = await _ask(
            scene, tmp_path, "What does the 10-K say about lease obligations?", provider=provider
        )

        assert question.tier == 3
        assert not question.is_answered
        assert "9999" in question.content["discarded"]

    async def test_a_reader_that_says_the_material_does_not_answer_is_believed(
        self, scene: dict[str, Any], tmp_path: Path
    ) -> None:
        await _valued(scene)
        excerpt = await _excerpt(scene, LEASES)
        provider = _provider(
            _answer(str(excerpt.id), answered=False, not_in_record=["the 2025 lease schedule"])
        )
        question = await _ask(
            scene, tmp_path, "What does the 10-K say about lease obligations?", provider=provider
        )

        assert question.tier == 3
        assert not question.is_answered
        assert "2025 lease schedule" in question.content["discarded"]

    async def test_the_note_behind_a_marker_is_the_excerpt_as_stored(
        self, scene: dict[str, Any], tmp_path: Path
    ) -> None:
        await _valued(scene)
        excerpt = await _excerpt(scene, LEASES)
        question = await _ask(
            scene,
            tmp_path,
            "What does the 10-K say about lease obligations?",
            provider=_provider(_answer(str(excerpt.id))),
        )
        note = await ask_service.note_of(scene["session"], question, 1)
        assert note is not None
        assert note.kind == "excerpt"
        assert note.source is not None
        assert note.source.id == scene["document"].id
        assert note.excerpt == LEASES or note.withheld
        assert await ask_service.note_of(scene["session"], question, 2) is None

    async def test_a_pass_that_would_cross_a_ceiling_stops_before_the_call(
        self, scene: dict[str, Any], tmp_path: Path
    ) -> None:
        await _valued(scene)
        excerpt = await _excerpt(scene, LEASES)
        provider = _provider(_answer(str(excerpt.id)))
        question = await _ask(
            scene,
            tmp_path,
            "What does the 10-K say about lease obligations?",
            provider=provider,
            per_run_budget_gbp=Decimal("0.0001"),
        )

        assert provider.call_count == 0
        assert question.tier == 2
        assert not question.is_answered
        assert question.content["kind"] == "stopped"
        job = await scene["session"].get(Job, question.job_id)
        assert job is not None
        assert job.status is JobStatus.FAILED

    async def test_a_record_with_nothing_to_deal_is_priced_without_a_call(
        self, scene: dict[str, Any], tmp_path: Path
    ) -> None:
        scene["request"].company_id = scene["company"].id
        scene["document"].quarantined = True
        scene["document"].quarantine_reason = "test"
        await scene["session"].flush()
        provider = _provider(_answer("x"))
        question = await _ask(scene, tmp_path, "Is the margin sustainable?", provider=provider)

        assert provider.call_count == 0
        assert question.tier == 3
        assert question.content["kind"] == "research"


# -- Tier 3, and the adversarial corpus --------------------------------------------------------

OUTSIDE_THE_RECORD = (
    "Has anything changed at their main competitor?",
    "How does Amazon compare on margins?",
    "What did management say on the Q3 call?",
    "What is the share price today?",
    "What does the proxy statement say about executive pay?",
    "What happened to revenue in 2019?",
)


class TestResearch:
    @pytest.mark.parametrize("text", OUTSIDE_THE_RECORD)
    async def test_a_question_outside_the_record_is_priced_and_never_answered(
        self, scene: dict[str, Any], tmp_path: Path, text: str
    ) -> None:
        """The adversarial corpus: no answer from the model's own knowledge, no call at all."""
        await _valued(scene)
        provider = _provider(_answer("anything", text="Amazon's margin was 5%."))
        question = await _ask(scene, tmp_path, text, provider=provider)

        assert provider.call_count == 0
        assert question.tier == 3
        assert not question.is_answered
        assert question.answer is None
        assert question.job_id is None
        assert question.approved_at is None
        assert question.estimated_cost_gbp is not None
        assert question.estimated_cost_gbp > 0
        assert question.content["kind"] == "research"
        assert question.content["sentence"].startswith("This needs new material. About £")
        assert "Contoso Corporation's record" in question.content["sentence"]

    def test_the_estimate_grows_with_what_the_record_lacks(self, tmp_path: Path) -> None:
        settings = _settings(tmp_path)
        one = resolve("Has anything changed?", RECORD)
        two = resolve("Has anything changed at Amazon since the Q3 call?", RECORD)
        first = ask_service.research_estimate(one, router=Router(settings), settings=settings)
        second = ask_service.research_estimate(two, router=Router(settings), settings=settings)

        assert first.searches == 2
        assert second.searches > first.searches
        assert second.cost_gbp > first.cost_gbp
        assert second.documents_up_to > first.documents_up_to
        assert first.cost_gbp == first.cost_gbp.quantize(Decimal("0.01"))

    async def test_a_blank_question_is_refused(self, scene: dict[str, Any], tmp_path: Path) -> None:
        await _valued(scene)
        with pytest.raises(ValidationError):
            await _ask(scene, tmp_path, "   ")


# -- The pages --------------------------------------------------------------------------------


@pytest.fixture
async def committed(db_engine: Any) -> Any:
    """The assumption scene on a session of its own, so what it commits reaches the app.

    ``db_session`` runs inside one outer transaction and its commits are savepoints, which
    the application's own connections cannot see; a page test needs rows that are there.
    """
    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    async with factory() as session:
        user = User(email="proposals@example.invalid", display_name="P", role=UserRole.OWNER)
        session.add(user)
        await session.flush()
        request = research_request(
            user_id=user.id,
            company_name="Contoso Corporation",
            ticker="CTSO",
            exchange="NASDAQ",
            as_of_date=date(2024, 6, 30),
            base_currency="USD",
            investment_horizon_months=12,
            max_cost_gbp="2.50",
            portfolio_context={},
        )
        company = Company(
            name="Contoso Corporation", ticker="CTSO", exchange="NASDAQ", cik="0000000002"
        )
        artefact = Artefact(
            sha256="d" * 64, size_bytes=10, media_type="application/json", storage_key="dd/d"
        )
        session.add_all([request, company, artefact])
        await session.flush()
        document = SourceDocument(
            work_order_id=request.id,
            artefact_id=artefact.id,
            url="https://data.sec.gov/api/xbrl/companyfacts/CIK0000000002.json",
            provider=Provider.SEC_EDGAR,
            source_tier=SourceTier.T1_REGULATORY,
            title="Contoso XBRL company facts",
            retrieved_at=datetime.now(UTC),
        )
        job = Job(
            work_order_id=request.id,
            workflow_version="test",
            code_version="abc",
            status=JobStatus.RUNNING,
            started_at=datetime.now(UTC),
        )
        session.add_all([document, job])
        await session.flush()
        yield {
            "session": session,
            "request": request,
            "company": company,
            "job": job,
            "document": document,
        }


@pytest.fixture
async def api(api_settings: Any, db_engine: Any, fake_redis: Any) -> Any:
    store = LocalArtefactStore(
        api_settings.artefact_root, max_bytes=api_settings.max_artefact_bytes
    )
    app = build_app(
        api_settings, engine=db_engine, redis=fake_redis, provider=_provider(), store=store
    )
    async for client in client_for(app):
        yield client


def _csrf(html: str) -> str:
    found = re.search(r'name="csrf_token"\s+value="([^"]+)"', html)
    assert found is not None, "the page rendered no CSRF token"
    return str(found.group(1))


class TestThePages:
    async def test_the_ask_page_offers_the_record_and_lists_nothing_yet(
        self, api: Any, committed: dict[str, Any]
    ) -> None:
        await _valued(committed)
        await committed["session"].commit()
        response = await api.get("/ask")

        assert response.status_code == 200
        assert "Contoso Corporation (CTSO)" in response.text
        assert 'id="ask-form"' in response.text
        assert "Nothing asked yet" in response.text

    async def test_a_tier_one_question_shows_its_figures_with_their_walks(
        self, api: Any, committed: dict[str, Any]
    ) -> None:
        await _valued(committed)
        await committed["session"].commit()
        page = await api.get("/ask")
        response = await api.post(
            "/ask",
            data={
                "csrf_token": _csrf(page.text),
                "company_id": str(committed["company"].id),
                "question": "What if the discount rate were half a point higher?",
            },
        )
        assert response.status_code == 303
        shown = await api.get(response.headers["location"])

        assert shown.status_code == 200
        assert "from the record" in shown.text
        assert 'id="figures"' in shown.text
        assert "/calculations/" in shown.text
        assert "Free. No model was called" in shown.text
        assert 'data-figure="6"' in shown.text

    async def test_a_tier_three_question_shows_its_price_and_the_go_ahead(
        self, api: Any, committed: dict[str, Any]
    ) -> None:
        await _valued(committed)
        await committed["session"].commit()
        page = await api.get("/ask")
        response = await api.post(
            "/ask",
            data={
                "csrf_token": _csrf(page.text),
                "company_id": str(committed["company"].id),
                "question": "Has anything changed at their main competitor?",
            },
        )
        shown = await api.get(response.headers["location"])

        assert shown.status_code == 200
        assert "needs new material" in shown.text
        assert "This needs new material. About £" in shown.text
        assert 'id="research-form"' in shown.text
        assert "Go ahead" in shown.text
        assert "not yet available" not in shown.text
        assert "Nothing has been spent." in shown.text
        listed = await api.get("/ask")
        assert 'data-tier="3"' in listed.text
        assert 'data-state="priced"' in listed.text

    async def test_a_note_page_shows_the_excerpt_and_its_source(
        self, api: Any, committed: dict[str, Any], tmp_path: Path
    ) -> None:
        await _valued(committed)
        excerpt = await _excerpt(committed, LEASES)
        question = await _ask(
            committed,
            tmp_path,
            "What does the 10-K say about lease obligations?",
            provider=_provider(_answer(str(excerpt.id))),
        )
        await committed["session"].commit()

        shown = await api.get(f"/ask/{question.id}")
        assert shown.status_code == 200
        assert "re-reading 1 document" in shown.text
        assert 'data-note="1"' in shown.text
        note = await api.get(f"/ask/{question.id}/notes/1")
        assert note.status_code == 200
        assert "Contoso Corporation 10-K 2024" in note.text
        assert "lease obligations" in note.text or "does not permit" in note.text
        missing = await api.get(f"/ask/{question.id}/notes/2")
        assert missing.status_code == 404

    async def test_a_company_outside_the_record_is_refused(
        self, api: Any, committed: dict[str, Any]
    ) -> None:
        await committed["session"].commit()
        page = await api.get("/ask")
        response = await api.post(
            "/ask",
            data={
                "csrf_token": _csrf(page.text),
                "company_id": str(uuid.uuid4()),
                "question": "Anything?",
            },
        )
        assert response.status_code == 404


# -- Tier 3 -----------------------------------------------------------------------------------

# A page on the host the scene's record already reads from, with one paragraph long enough
# to be an excerpt and no numeral the reader could be accused of inventing.
ANNOUNCEMENT = (
    "Contoso announced that it will acquire Fabrikam for cash from existing balances, and "
    "that the board expects the transaction to close in the second half of the year, adding "
    "to earnings from the first full year of ownership subject to the customary approvals."
)
PAGE = f"<html><body><h1>Contoso to acquire Fabrikam</h1><p>{ANNOUNCEMENT}</p></body></html>"
FETCHED_URL = "https://data.sec.gov/news/contoso-fabrikam.htm"
STRANGER_URL = "https://news.example.com/contoso-results"
RESEARCH_QUESTION = "Has anything changed at their main competitor?"


class _ResearchBrain:
    """The worker searches, fetches one page on the record's own host and one it may not,
    then reports; the reader cites the newest excerpt it was dealt."""

    def __init__(self, *, answered: bool = True) -> None:
        self.provider: FakeProvider | None = None
        self.answered = answered
        self.turns = [
            WorkerTurn(
                requests=[
                    ToolRequest(tool="web_search", query="Contoso Fabrikam", why="what is new")
                ]
            ),
            WorkerTurn(
                requests=[
                    ToolRequest(tool="fetch_known_url", query=FETCHED_URL, why="read it"),
                    ToolRequest(tool="fetch_known_url", query=STRANGER_URL, why="and this"),
                ]
            ),
            WorkerTurn(
                report=WorkerReport(
                    findings=[],
                    leads=[WorkerLead(question="The terms?", why_it_matters="They price it.")],
                    coverage_note="Read one announcement on the record's own host.",
                )
            ),
        ]

    def __call__(self, schema: type[Any]) -> Any:
        if schema is WorkerTurn:
            return self.turns.pop(0)
        assert self.provider is not None
        shown = " ".join(
            str(message.get("cache_prefix") or "") + str(message.get("content") or "")
            for message in self.provider.calls[-1]["messages"]
        )
        dealt = re.findall(r"extraction ([0-9a-f-]{36})", shown)
        assert dealt, "the reader was dealt no excerpt"
        return AskAnswer(
            answered=self.answered,
            paragraphs=[
                AnswerParagraph(
                    text=(
                        "The announcement says the Fabrikam purchase is funded from cash and "
                        "expected to close in the second half."
                    ),
                    cites=[dealt[-1]],
                )
            ],
            not_in_record=[] if self.answered else ["a closing date"],
        )


def _research_provider(*, answered: bool = True) -> FakeProvider:
    brain = _ResearchBrain(answered=answered)
    provider = FakeProvider(brain, inspect_schema=refuse_unanswerable_schema)
    brain.provider = provider
    return provider


async def _priced(scene: dict[str, Any], tmp_path: Path) -> Any:
    await _valued(scene)
    question = await _ask(scene, tmp_path, RESEARCH_QUESTION)
    assert question.tier == 3
    assert question.content["kind"] == "research"
    return question


async def _researched(
    scene: dict[str, Any], tmp_path: Path, question: Any, *, provider: FakeProvider
) -> tuple[Any, _RecordingFetcher]:
    settings = _settings(tmp_path)
    store = LocalArtefactStore(settings.artefact_root, max_bytes=settings.max_artefact_bytes)
    fetcher = _RecordingFetcher(body=PAGE.encode())
    fetcher.store = store
    answered = await ask_service.research(
        scene["session"],
        settings=settings,
        provider=provider,
        router=Router(settings),
        store=store,
        fetcher=fetcher,
        user=await _owner(scene),
        company=scene["company"],
        question=question,
    )
    return answered, fetcher


class TestTheThirdTierRuns:
    async def test_the_go_ahead_carries_the_price_shown_and_happens_once(
        self, scene: dict[str, Any], tmp_path: Path
    ) -> None:
        question = await _priced(scene, tmp_path)
        owner = await _owner(scene)
        shown = ask_service.estimate_hash_of(question)

        with pytest.raises(ValidationError, match="not the price on record"):
            await ask_service.approve_research(
                scene["session"], question=question, user=owner, estimate_hash="a" * 64
            )
        assert not question.is_approved

        await ask_service.approve_research(
            scene["session"], question=question, user=owner, estimate_hash=shown
        )
        assert question.is_approved
        assert question.content["approved"]["estimate_hash"] == shown
        assert question.content["approved"]["report_id"]

        with pytest.raises(ConflictError, match="runs once"):
            await ask_service.approve_research(
                scene["session"], question=question, user=owner, estimate_hash=shown
            )

    async def test_it_is_refused_for_another_account_a_dealt_question_and_no_current_report(
        self, scene: dict[str, Any], tmp_path: Path
    ) -> None:
        question = await _priced(scene, tmp_path)
        shown = ask_service.estimate_hash_of(question)
        stranger = User(email="other@example.invalid", display_name="Other", role=UserRole.OWNER)
        scene["session"].add(stranger)
        await scene["session"].flush()
        with pytest.raises(ConflictError, match="not in your account"):
            await ask_service.approve_research(
                scene["session"], question=question, user=stranger, estimate_hash=shown
            )

        dealt = await _ask(scene, tmp_path, "What if the discount rate were half a point higher?")
        assert dealt.tier == 1
        with pytest.raises(ConflictError, match="dealt with from the record"):
            await ask_service.approve_research(
                scene["session"],
                question=dealt,
                user=await _owner(scene),
                estimate_hash=ask_service.estimate_hash_of(dealt),
            )

        with pytest.raises(ConflictError, match="go-ahead"):
            await _researched(scene, tmp_path, question, provider=_research_provider())

    async def test_a_company_without_a_current_report_cannot_be_researched(
        self, scene: dict[str, Any], tmp_path: Path
    ) -> None:
        scene["request"].company_id = scene["company"].id
        await scene["session"].flush()
        question = await _ask(scene, tmp_path, RESEARCH_QUESTION)
        assert question.tier == 3
        with pytest.raises(ConflictError, match="no current report"):
            await ask_service.approve_research(
                scene["session"],
                question=question,
                user=await _owner(scene),
                estimate_hash=ask_service.estimate_hash_of(question),
            )

    async def test_it_acquires_through_the_report_and_answers_citing_what_it_fetched(
        self, scene: dict[str, Any], tmp_path: Path
    ) -> None:
        question = await _priced(scene, tmp_path)
        owner = await _owner(scene)
        await ask_service.approve_research(
            scene["session"],
            question=question,
            user=owner,
            estimate_hash=ask_service.estimate_hash_of(question),
        )
        provider = _research_provider()

        answered, fetcher = await _researched(scene, tmp_path, question, provider=provider)

        session: AsyncSession = scene["session"]
        assert answered.is_answered
        assert answered.content["kind"] == "research"
        assert answered.content["outcome"] == "answered"
        assert "second half" in str(answered.answer)
        # The searches and the fetches went through the run's own executors: the host the
        # record reads from was admitted, the search engine's host was refused unread.
        assert len(provider.web_searches) == 1
        assert fetcher.urls == [FETCHED_URL]
        assert answered.content["searches"] == 1
        # One document, recorded under the report's request with this question's job as
        # the fetcher of record, excerpted so the reader could cite it.
        assert len(answered.documents_added or []) == 1
        [document_id] = answered.documents_added
        document = await session.get(SourceDocument, uuid.UUID(document_id))
        assert document is not None
        assert document.work_order_id == scene["request"].id
        assert document.job_id == answered.job_id
        assert document.url == FETCHED_URL
        excerpts = list(
            await session.scalars(
                select(Extraction).where(Extraction.source_document_id == document.id)
            )
        )
        assert excerpts, "the fetched page was not excerpted"
        assert any(ANNOUNCEMENT[:40] in row.excerpt for row in excerpts)
        note = answered.content["notes"][0]
        assert note["kind"] == "excerpt"
        assert note["id"] in {str(row.id) for row in excerpts}
        assert answered.content["paragraphs"][0]["notes"] == [1]
        assert answered.content["documents"][0]["id"] == document_id
        # Priced before, metered after: the search fee, the worker's turns and the reader's
        # pass all land on the question's own job.
        assert answered.actual_cost_gbp is not None
        assert answered.actual_cost_gbp > 0
        job = await session.get(Job, answered.job_id)
        assert job is not None
        assert job.status is JobStatus.SUCCEEDED
        costs = await session.scalar(
            select(func.count()).select_from(Cost).where(Cost.job_id == job.id)
        )
        assert costs is not None
        assert costs >= 3
        # The question is now in the record: the next one resolves over the larger record.
        record = await ask_service.held_record(session, user=owner, company=scene["company"])
        assert "fabrikam" in record.vocabulary

        with pytest.raises(ConflictError, match="runs once"):
            await _researched(scene, tmp_path, question, provider=_research_provider())

    async def test_nothing_useful_is_an_honest_answer_and_the_documents_stay(
        self, scene: dict[str, Any], tmp_path: Path
    ) -> None:
        question = await _priced(scene, tmp_path)
        await ask_service.approve_research(
            scene["session"],
            question=question,
            user=await _owner(scene),
            estimate_hash=ask_service.estimate_hash_of(question),
        )

        answered, fetcher = await _researched(
            scene, tmp_path, question, provider=_research_provider(answered=False)
        )

        assert answered.is_answered
        assert answered.content["outcome"] == "nothing_useful"
        assert str(answered.answer).startswith("Nothing that was found answers this. 1 document")
        assert answered.content["paragraphs"] == []
        assert len(answered.documents_added or []) == 1
        assert fetcher.urls == [FETCHED_URL]
        assert answered.actual_cost_gbp is not None
        assert answered.actual_cost_gbp > 0
        job = await scene["session"].get(Job, answered.job_id)
        assert job is not None
        assert job.status is JobStatus.SUCCEEDED

    async def test_the_worker_is_briefed_with_the_question_and_bounded_by_the_estimate(
        self, scene: dict[str, Any], tmp_path: Path
    ) -> None:
        question = await _priced(scene, tmp_path)
        await ask_service.approve_research(
            scene["session"],
            question=question,
            user=await _owner(scene),
            estimate_hash=ask_service.estimate_hash_of(question),
        )
        provider = _research_provider()

        await _researched(scene, tmp_path, question, provider=provider)

        first = provider.calls[0]
        assert "Topic: question." in first["messages"][0]["content"]
        assert f"The question to answer: {RESEARCH_QUESTION}" in first["messages"][0]["content"]
        assert "One question the operator asked" in first["system"]
        estimate = question.content["estimate"]
        budget = int(estimate["searches"]) + int(estimate["documents_up_to"])
        assert f"Remaining tool budget: {budget} call(s)" in first["messages"][0]["content"]


class TestTheResearchPages:
    async def test_the_go_ahead_starts_the_research_and_the_page_says_so(
        self, api: Any, committed: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        queued: list[str] = []

        async def record(redis: Any, question_id: uuid.UUID) -> str:
            queued.append(str(question_id))
            return f"task-{question_id}"

        monkeypatch.setattr("aer.web.ask.pages.enqueue_ask", record)
        await _valued(committed)
        await committed["session"].commit()
        page = await api.get("/ask")
        asked = await api.post(
            "/ask",
            data={
                "csrf_token": _csrf(page.text),
                "company_id": str(committed["company"].id),
                "question": RESEARCH_QUESTION,
            },
        )
        shown = await api.get(asked.headers["location"])
        assert 'id="research-form"' in shown.text
        assert "not yet available" not in shown.text
        hidden = re.search(r'name="estimate_hash"\s+value="([0-9a-f]{64})"', shown.text)
        assert hidden is not None

        started = await api.post(
            f"{asked.headers['location']}/research",
            data={"csrf_token": _csrf(shown.text), "estimate_hash": hidden.group(1)},
            follow_redirects=False,
        )
        assert started.status_code == 303, started.text
        assert queued == [asked.headers["location"].rsplit("/", 1)[-1]]

        again = await api.get(asked.headers["location"])
        assert 'id="research-form"' not in again.text
        assert 'id="researching"' in again.text
        assert "being researched" in again.text
        listed = await api.get("/ask")
        assert 'data-state="researching"' in listed.text

        index = await api.get("/ask")
        twice = await api.post(
            f"{asked.headers['location']}/research",
            data={"csrf_token": _csrf(index.text), "estimate_hash": hidden.group(1)},
            follow_redirects=False,
        )
        assert twice.status_code == 409
        assert queued == [asked.headers["location"].rsplit("/", 1)[-1]]

    async def test_a_go_ahead_at_a_different_price_is_refused(
        self, api: Any, committed: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        queued: list[str] = []

        async def record(redis: Any, question_id: uuid.UUID) -> str:
            queued.append(str(question_id))
            return "task"

        monkeypatch.setattr("aer.web.ask.pages.enqueue_ask", record)
        await _valued(committed)
        await committed["session"].commit()
        page = await api.get("/ask")
        asked = await api.post(
            "/ask",
            data={
                "csrf_token": _csrf(page.text),
                "company_id": str(committed["company"].id),
                "question": RESEARCH_QUESTION,
            },
        )
        shown = await api.get(asked.headers["location"])

        refused = await api.post(
            f"{asked.headers['location']}/research",
            data={"csrf_token": _csrf(shown.text), "estimate_hash": "f" * 64},
            follow_redirects=False,
        )
        assert refused.status_code == 422
        assert "not the price on record" in refused.text
        assert queued == []
        still = await api.get(asked.headers["location"])
        assert 'id="research-form"' in still.text
