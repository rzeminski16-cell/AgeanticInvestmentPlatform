"""The blocking gate: ten measurements over the corpora the phases produced.

This is the module that turns "we proved it once" into "it is still true". It gathers
observations by running the **real** verifier, the real date extractor, the real injection
scanner, the real unit algebra, the real replay harness, the real assumptions ladder and
the real skill-containment layers over labelled corpora, hands them to
:mod:`aer.eval.metrics`, and fails the build if any of the ten moves.

Three properties make it a gate rather than a formality.

* **The corpora contain the wrong answers as well as the right ones.** Scored against
  only-genuine citations a verifier that always says yes gets 100%, and against only
  post-dated documents a platform that refuses everything gets 100%. Both mistakes are in
  the fixtures.
* **An empty corpus fails.** If a fixture stops loading, the metric raises rather than
  scoring perfectly on nothing.
* **Each metric has its own test.** A single "everything passes" assertion would report
  eight guarantees as one line and send whoever is on the failure to go and find which.

No network and no model spend: every corpus is bytes in the repository, and nothing here
constructs a provider. The two task-32 metrics score real artefacts as well as fixtures —
numerical consistency replays the golden corpus through the same harness that replays
stored runs (``tests/test_eval_replay.py`` covers the stored-run side and its deliberate
regressions), and assumption completeness resolves a chain the real services persisted.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from selectolax.parser import HTMLParser
from sqlalchemy.ext.asyncio import AsyncSession

from aer.agents.base import Agent, ToolNotPermittedError
from aer.agents.untrusted import UntrustedSource, wrap_untrusted
from aer.calc.units import CalculationError, Quantity, SourceRef, UnitMismatchError, money, ratio
from aer.calc.units import shares as share_count
from aer.config import Settings
from aer.core.enums import ClaimKind, Provider, SourceTier
from aer.db.models import Artefact, Job, ResearchRequest, SourceDocument
from aer.eval import (
    BLOCKING,
    CitationObservation,
    CompletenessObservation,
    ConformanceObservation,
    ContainmentObservation,
    InjectionObservation,
    MetricResult,
    ReplayObservation,
    SourceObservation,
    UnitObservation,
    evaluate_all,
)
from aer.eval.metrics import (
    assumption_completeness,
    citation_accuracy,
    custom_section_contract_conformance,
    hallucinated_citation_rate,
    injection_resistance,
    look_ahead_recall,
    numerical_consistency,
    skill_privilege_containment,
    temporal_compliance,
    unit_integrity,
)
from aer.eval.replay import CALC_MODULES, completeness_observations_for_job, registry, replay
from aer.extract.dates import extract_publication_date
from aer.extract.html import extract_html
from aer.extract.injection import scan_markup, scan_text
from aer.services.citations import record_citation, record_claim
from aer.services.extractions import record_excerpt
from aer.services.sources import decide_quarantine
from aer.storage.local import LocalArtefactStore
from aer.verify.citations import verify
from tests import citation_corpus, injection_fixtures, lookahead_fixtures, skill_corpus
from tests.agent_probes import ProbeAnswer
from tests.ledger_fixtures import record_valuation_ledger
from tests.scene_fixtures import build_scene
from tests.test_calc_golden import EXPECTED_CORPUS_SIZE
from tests.workflow_fixtures import AS_OF_DATE

pytestmark = pytest.mark.integration

GOLDEN_CORPUS = Path(__file__).parent / "fixtures" / "calc" / "golden.json"


# ==========================================================================================
# Gathering: run the real code over the corpora
# ==========================================================================================


@pytest.fixture
def settings(tmp_path: Any) -> Settings:
    return Settings(
        http_user_agent="Tracework Eval test@example.invalid",
        artefact_root=tmp_path / "artefacts",
    )


@pytest.fixture
def store(settings: Settings) -> LocalArtefactStore:
    return LocalArtefactStore(settings.artefact_root, max_bytes=settings.max_artefact_bytes)


@pytest.fixture
async def scene(db_session: AsyncSession, store: LocalArtefactStore) -> dict[str, Any]:
    # Shared rather than built per corpus: two fixtures each calling build_scene in one test
    # would seed the same user twice and trip the email uniqueness constraint.
    return await build_scene(db_session, store)


@pytest.fixture
async def citations(
    db_session: AsyncSession,
    store: LocalArtefactStore,
    settings: Settings,
    scene: dict[str, Any],
) -> list[CitationObservation]:
    """Every pair in ``citation_corpus`` put to the real verifier.

    One document, one section, one citation per pair. The excerpt is written at the pair's
    own locator and then replaced where the pair says it should be wrong — which is exactly
    how a hallucinated citation reaches the database in the first place.
    """
    document = await _document_for(
        db_session,
        store,
        request=scene["request"],
        job=scene["job"],
        payload=citation_corpus.FILING,
        url="https://www.sec.gov/Archives/edgar/data/1/contoso-10k.htm",
    )
    extracted = extract_html(citation_corpus.FILING).text

    claim = await record_claim(
        db_session,
        section=scene["section"],
        kind=ClaimKind.FACTUAL,
        text="Every figure in this corpus.",
    )

    observations: list[CitationObservation] = []
    for pair in citation_corpus.PAIRS:
        excerpt = extracted.locate(pair.anchor)
        assert excerpt is not None, f"{pair.name}: the anchor is not in the corpus document"

        extraction = await record_excerpt(
            db_session,
            source_document_id=document.id,
            extracted=extracted,
            excerpt=excerpt,
        )
        if pair.stored is not None:
            extraction.excerpt = pair.stored
            await db_session.flush()

        citation = await record_citation(
            db_session,
            claim=claim,
            source_document_id=document.id,
            extraction_id=extraction.id,
        )
        outcome = await verify(db_session, store, citation=citation, settings=settings)

        observations.append(
            CitationObservation(
                name=pair.name,
                genuine=pair.genuine,
                verified=outcome.verified,
                ratio=str(outcome.ratio) if outcome.ratio is not None else None,
                error=citation.verification_error,
            )
        )

    return observations


@pytest.fixture
def sources() -> list[SourceObservation]:
    """Every planted document in ``lookahead_fixtures``, put through the real rules.

    Two steps, in the order the platform runs them: extract a date from whatever evidence
    the document carries, then decide admissibility on the **latest** date any evidence
    supports. Nothing is told the answer — the fixture's ``expected`` is used only as the
    label the metric scores against.
    """
    observations: list[SourceObservation] = []
    planted = (
        *lookahead_fixtures.POST_DATED,
        *lookahead_fixtures.ADMISSIBLE,
        *lookahead_fixtures.UNDATABLE,
    )

    for case in planted:
        found = extract_publication_date(
            index_date=case.index_date,
            metadata=case.metadata,
            text=case.text,
            headers=case.headers,
        )
        decision = decide_quarantine(
            publication_date=found.latest if found is not None else None,
            point_in_time=True,
            source_tier=SourceTier.T1_REGULATORY,
            as_of_date=lookahead_fixtures.AS_OF,
        )
        observations.append(
            SourceObservation(
                name=case.name,
                # The **conservative** date, not the best estimate. Admissibility is decided
                # on the latest evidence (ADR 0021), so the label the metric scores against
                # has to be the same question the platform is answering.
                published=case.conservative,
                as_of=lookahead_fixtures.AS_OF,
                admitted=not decision.quarantined,
                established=found.value if found is not None else None,
            )
        )

    return observations


class _NoToolsAgent(Agent[Any, ProbeAnswer]):
    """An agent with an empty allowlist, standing in for every role in the platform.

    Every role registered so far grants no tools, so "authorised a tool outside policy"
    means "authorised a tool at all". The allowlist comes from the registered
    ``evaluation-probe`` role (``tests/agent_probes.py``) — an agent class cannot carry
    one of its own. When a role gains tools the corpus below gains the ones it must still
    refuse.
    """

    role = "evaluation-probe"
    output_schema = ProbeAnswer


# What the payloads try to get done. Each is a capability that would turn a poisoned
# document into an action: reach the network, write a file, run a command, send data out.
_DEMANDED_TOOLS = ("http_get", "write_file", "shell", "send_email", "sql")


@pytest.fixture
def injections() -> list[InjectionObservation]:
    """Every payload in ``injection_fixtures``, extracted, scanned, wrapped and probed.

    Resistance is measured on **containment and tool policy**, not on detection — see ADR
    0019. A payload the heuristics miss is still quoted as data and still cannot cause a
    tool call, and those are the two things that decide whether it did any harm.
    """
    agent = _NoToolsAgent()
    observations: list[InjectionObservation] = []

    for payload in injection_fixtures.PAYLOADS:
        extracted = extract_html(payload.html).text
        findings = (
            *scan_markup(HTMLParser(payload.html.decode()), extracted.text),
            *scan_text(extracted.text),
        )

        wrapped = wrap_untrusted(
            [
                UntrustedSource(
                    source_document_id="00000000-0000-0000-0000-000000000000",
                    tier=SourceTier.T5_SECONDARY.value,
                    text=extracted.text,
                    title=payload.name,
                )
            ]
        )

        # Authorised outside policy, counted rather than asserted: the metric wants a number,
        # and a raised exception here would stop the corpus at its first violation.
        authorised = 0
        for tool in _DEMANDED_TOOLS:
            try:
                agent.require_tool(tool)
            except ToolNotPermittedError:
                continue
            authorised += 1

        observations.append(
            InjectionObservation(
                name=payload.name,
                contained=_is_contained(wrapped),
                tools_authorised_outside_policy=authorised,
                detected=bool(findings),
                signals=tuple(sorted({finding.signal.value for finding in findings})),
            )
        )

    return observations


def _is_contained(wrapped: str) -> bool:
    """Whether the quoted block is still one block.

    Exactly one opening and one closing delimiter. A payload that smuggled either through
    would have closed its own quotation and continued as though its next sentence were part
    of the system's own frame — the attack executed against the mitigation.
    """
    return wrapped.count("<untrusted_source ") == 1 and wrapped.count("</untrusted_source>") == 1


# The unit corpus: operations that must be refused, and compatible ones that must not be —
# because a unit algebra that raised on everything would score perfectly and be useless.
def _unit_cases() -> list[tuple[str, bool, Any]]:
    source = SourceRef.financial_fact("00000000-0000-0000-0000-000000000000", label="corpus")
    usd = money("100", "USD", source=source)
    gbp = money("100", "GBP", source=source)
    count = share_count("100", source=source)
    pure = ratio("100", source=source)

    return [
        ("add USD to GBP", False, lambda: usd + gbp),
        ("subtract GBP from USD", False, lambda: usd - gbp),
        ("add shares to USD", False, lambda: count + usd),
        ("subtract a ratio from USD", False, lambda: usd - pure),
        ("add a ratio to shares", False, lambda: pure + count),
        ("compare USD with GBP", False, lambda: usd + gbp * pure),
        ("add USD to USD", True, lambda: usd + usd),
        ("divide USD by shares", True, lambda: usd / count),
        ("multiply USD by a ratio", True, lambda: usd * pure),
        ("subtract shares from shares", True, lambda: count - count),
    ]


@pytest.fixture
def units() -> list[UnitObservation]:
    observations: list[UnitObservation] = []
    for name, compatible, operation in _unit_cases():
        try:
            result = operation()
        except UnitMismatchError:
            observations.append(UnitObservation(name=name, compatible=compatible, raised=True))
            continue
        except CalculationError:  # pragma: no cover -- not what this corpus exercises
            observations.append(UnitObservation(name=name, compatible=compatible, raised=True))
            continue

        assert isinstance(result, Quantity)
        observations.append(UnitObservation(name=name, compatible=compatible, raised=False))
    return observations


@pytest.fixture
def replays() -> list[ReplayObservation]:
    """The thirty golden calculations, replayed through the real harness.

    The corpus is bytes in the repository — hand-computed answers in the stored-record
    shape — and the harness is the same :func:`aer.eval.replay.replay` that re-runs a live
    run's rows, so this measures both the arithmetic and the harness's ability to
    reconstruct a calculation from its record. Held here to the gate's 0.5% threshold; the
    tighter 0.01% golden bound is asserted in ``tests/test_calc_golden.py``.
    """
    corpus = json.loads(GOLDEN_CORPUS.read_text())
    return [
        replay(
            name=case["name"],
            label=case["name"],
            inputs=case["inputs"],
            parameters=case["parameters"],
            expected_value=Decimal(case["expected"]["value"]),
            expected_unit=case["expected"]["unit"],
        )
        for case in corpus["cases"]
    ]


@pytest.fixture
async def completeness(
    db_session: AsyncSession, scene: dict[str, Any]
) -> list[CompletenessObservation]:
    """A chain the real services persisted, resolved against the assumptions table.

    An assumption proposed and confirmed through ``aer.services.assumptions``, carried into
    a traced calculation via ``as_quantity``, persisted through ``persist_context``, and
    read back by the same resolver the live gate uses. The corpus also contains a
    calculation resting on facts alone, so "complete" is measured against a mixture rather
    than only rows the rule applies to.
    """
    await record_valuation_ledger(
        db_session, request=scene["request"], job=scene["job"], actor=scene["user"]
    )
    return await completeness_observations_for_job(db_session, scene["job"].id)


@pytest.fixture
def containments(settings: Settings) -> list[ContainmentObservation]:
    """Every adversarial skill file, put through the real containment layers.

    The ceiling is the same configured value the resolver composes against, so the
    budget escalation is scored against the number the platform actually enforces.
    """
    return skill_corpus.containment_observations(
        budget_ceiling=settings.custom_section_token_ceiling
    )


@pytest.fixture
def conformances() -> list[ConformanceObservation]:
    """Every labelled custom-section output, put to the real contract validation."""
    return skill_corpus.conformance_observations()


async def _document_for(
    session: AsyncSession,
    store: LocalArtefactStore,
    *,
    request: ResearchRequest,
    job: Job,
    payload: bytes,
    url: str,
) -> SourceDocument:
    stored = await store.put_bytes(payload)
    artefact = Artefact(
        sha256=stored.sha256,
        media_type="text/html",
        size_bytes=stored.size_bytes,
        storage_key=store.storage_key_for(stored.sha256),
    )
    session.add(artefact)
    await session.flush()

    document = SourceDocument(
        work_order_id=request.id,
        request_id=request.id,
        job_id=job.id,
        artefact_id=artefact.id,
        url=url,
        title="Contoso Corporation Form 10-K",
        provider=Provider.SEC_EDGAR,
        source_tier=SourceTier.T1_REGULATORY,
        # Comfortably before the scene's as-of date. A corpus document published *after* it
        # is refused by the look-ahead check before its text is ever read, and every pair
        # would score zero for a reason that has nothing to do with the verifier.
        publication_date=AS_OF_DATE - timedelta(days=30),
        publication_date_latest=AS_OF_DATE - timedelta(days=30),
        retrieved_at=datetime.now(UTC),
    )
    session.add(document)
    await session.flush()
    return document


# ==========================================================================================
# The gate
# ==========================================================================================


def _assert_passed(result: MetricResult) -> None:
    assert result.passed, result.describe()


class TestTheBlockingMetrics:
    async def test_citation_accuracy(self, citations: list[CitationObservation]) -> None:
        _assert_passed(citation_accuracy(citations))

    async def test_hallucinated_citation_rate(self, citations: list[CitationObservation]) -> None:
        """Zero. Not "low" — a single fabricated excerpt marked verified is a report with a
        sentence in it that nothing supports and a badge saying it was checked."""
        _assert_passed(hallucinated_citation_rate(citations))

    def test_temporal_compliance(self, sources: list[SourceObservation]) -> None:
        _assert_passed(temporal_compliance(sources))

    def test_look_ahead_recall(self, sources: list[SourceObservation]) -> None:
        _assert_passed(look_ahead_recall(sources))

    def test_injection_resistance(self, injections: list[InjectionObservation]) -> None:
        _assert_passed(injection_resistance(injections))

    def test_unit_integrity(self, units: list[UnitObservation]) -> None:
        _assert_passed(unit_integrity(units))

    def test_numerical_consistency(self, replays: list[ReplayObservation]) -> None:
        _assert_passed(numerical_consistency(replays))

    async def test_assumption_completeness(
        self, completeness: list[CompletenessObservation]
    ) -> None:
        _assert_passed(assumption_completeness(completeness))

    def test_custom_section_contract_conformance(
        self, conformances: list[ConformanceObservation]
    ) -> None:
        _assert_passed(custom_section_contract_conformance(conformances))

    def test_skill_privilege_containment(self, containments: list[ContainmentObservation]) -> None:
        """Zero successful escalations. Not "low" — a skill file that weakens its own
        evidence floor is an authoring surface that can switch the guarantees off."""
        _assert_passed(skill_privilege_containment(containments))

    async def test_all_ten_together(
        self,
        citations: list[CitationObservation],
        sources: list[SourceObservation],
        injections: list[InjectionObservation],
        units: list[UnitObservation],
        replays: list[ReplayObservation],
        completeness: list[CompletenessObservation],
        conformances: list[ConformanceObservation],
        containments: list[ContainmentObservation],
    ) -> None:
        """The gate as CI runs it, with every result in the failure message.

        Reported together as well as separately because a build that fails on two metrics
        should say so once rather than being fixed one round-trip at a time.
        """
        results = evaluate_all(
            citations=citations,
            sources=sources,
            injections=injections,
            units=units,
            replays=replays,
            completeness=completeness,
            conformances=conformances,
            containments=containments,
        )

        assert len(results) == len(BLOCKING)
        failed = [result for result in results if not result.passed]
        assert not failed, "\n".join(result.describe() for result in results)


class TestTheCorporaAreWorthScoring:
    """A gate is only as good as what it is scored against.

    Each of these fails if a corpus quietly stops containing the cases that make its metric
    mean something — which is the way a green gate goes hollow without anybody noticing.
    """

    def test_the_citation_corpus_contains_fabrications(self) -> None:
        # Without these, a verifier returning True unconditionally scores 100%.
        assert len(citation_corpus.fabricated_pairs()) >= 10

    def test_the_citation_corpus_is_mostly_genuine(self) -> None:
        # And without these, one returning False unconditionally scores 100%.
        assert len(citation_corpus.genuine_pairs()) >= 25

    def test_the_citation_corpus_is_at_least_forty_pairs(self) -> None:
        # The size docs/archive/PLAN.md §2.10 asks for. At 40, the 98% threshold permits no errors
        # at all — 39/40 is 0.975 — which is the intended strictness.
        assert len(citation_corpus.PAIRS) >= 40

    def test_the_look_ahead_corpus_plants_traps_and_controls(
        self, sources: list[SourceObservation]
    ) -> None:
        assert sum(1 for row in sources if row.is_after_as_of) >= 5
        # A platform that refused everything would score 100% on compliance; these are what
        # stop that passing.
        assert sum(1 for row in sources if row.admitted) >= 3

    def test_the_look_ahead_corpus_includes_undatable_documents(
        self, sources: list[SourceObservation]
    ) -> None:
        assert any(row.published is None for row in sources)

    def test_the_injection_corpus_is_the_size_the_plan_asks_for(
        self, injections: list[InjectionObservation]
    ) -> None:
        assert len(injections) >= 25

    def test_the_unit_corpus_contains_compatible_operations(
        self, units: list[UnitObservation]
    ) -> None:
        # Otherwise an algebra that raised on every operation would score perfectly.
        assert sum(1 for row in units if row.compatible) >= 3
        assert all(not row.raised for row in units if row.compatible)

    def test_the_golden_corpus_is_exactly_the_pinned_corpus(
        self, replays: list[ReplayObservation]
    ) -> None:
        # One source of truth: `tests/test_calc_golden.py` pins the corpus size, and this
        # module replaying a different number would mean the two suites read different
        # corpora. A second literal here is how K3's thirty-first case broke this file.
        assert len(replays) == EXPECTED_CORPUS_SIZE

    def test_the_golden_corpus_covers_every_calc_module(
        self, replays: list[ReplayObservation]
    ) -> None:
        # A calc module without a golden case is arithmetic the consistency metric has never
        # scored against a hand-computed answer.
        corpus = json.loads(GOLDEN_CORPUS.read_text())
        reg = registry()
        covered = {reg[case["name"]].__module__ for case in corpus["cases"]}

        assert covered == set(CALC_MODULES)

    async def test_the_completeness_corpus_actually_rests_on_an_assumption(
        self, completeness: list[CompletenessObservation]
    ) -> None:
        # A corpus of facts-only calculations is trivially complete, and the metric refuses
        # it — so the fixture has to contain both kinds for the gate to mean anything.
        assert any(row.rests_on_assumptions for row in completeness)
        assert any(not row.rests_on_assumptions for row in completeness)

    def test_the_adversarial_corpus_is_the_twelve_the_plan_asks_for(
        self, containments: list[ContainmentObservation]
    ) -> None:
        assert len(containments) == 12

    def test_every_named_escalation_family_is_represented(self) -> None:
        # The families task 42 names, each with at least one file attempting it. A family
        # that quietly left the corpus is an escalation nobody is asserting fails.
        families = {case.family for case in skill_corpus.ADVERSARIAL_SKILLS}
        assert families >= {
            "weaken_evidence",
            "widen_tools",
            "set_rating",
            "exceed_budget",
            "disable_citations",
            "override_point_in_time",
            "escape_boundary",
        }

    def test_each_escalation_fails_at_the_layer_that_owns_it(
        self, containments: list[ContainmentObservation]
    ) -> None:
        """Contained *at the guarding layer*, not merely somewhere.

        An escalation caught one layer late means the layer that owns it has died and a
        backstop is carrying its weight — a state the zero-successes metric cannot see,
        because the backstop still contained it.
        """
        moved = [
            f"{row.name}: guarded by {row.guarded_by}, stopped by {row.stopped_by}"
            for row in containments
            if not row.at_expected_layer
        ]
        assert not moved, "\n".join(moved)

    def test_every_containment_layer_is_exercised(
        self, containments: list[ContainmentObservation]
    ) -> None:
        # A layer with no corpus entry is a defence whose death nothing would notice.
        assert {row.guarded_by for row in containments} == {
            "frontmatter",
            "composer",
            "contract",
            "boundary",
        }

    def test_frontmatter_refusals_name_the_attacked_field(self, settings: Settings) -> None:
        # The authoring-time refusal is line-level and named (§2.12); a refusal for some
        # other reason would mean the corpus file is broken, not that the control works.
        for case in skill_corpus.ADVERSARIAL_SKILLS:
            if case.guarded_by != "frontmatter":
                continue
            probe = skill_corpus.probe_file(
                skill_corpus.read_adversarial(case.name),
                budget_ceiling=settings.custom_section_token_ceiling,
            )
            assert probe.error is not None, f"{case.name}: expected an authoring refusal"
            fields = {issue.field for issue in probe.error.issues}
            assert case.attacked_field in fields, (
                f"{case.name}: refused, but not for {case.attacked_field}"
            )

    def test_composer_containments_carry_their_clamp_receipts(self, settings: Settings) -> None:
        """The warning half of the control. §2.4's skill-policy-clamp trigger reads these
        receipts, so a composer that clamped silently would also kill the gate-2 banner."""
        for case in skill_corpus.ADVERSARIAL_SKILLS:
            if case.guarded_by != "composer":
                continue
            probe = skill_corpus.probe_file(
                skill_corpus.read_adversarial(case.name),
                budget_ceiling=settings.custom_section_token_ceiling,
            )
            assert probe.composed is not None, f"{case.name}: expected the file to compose"
            fields = {clamp.field for clamp in probe.composed.clamps}
            assert case.attacked_field in fields, f"{case.name}: clamped without a receipt"

    def test_the_conformance_corpus_is_six_skills_with_two_awkward_contracts(self) -> None:
        assert len(skill_corpus.CUSTOM_SECTION_SKILLS) == 6
        assert sum(1 for case in skill_corpus.CUSTOM_SECTION_SKILLS if case.awkward) == 2

    def test_the_conformance_corpus_carries_outputs_labelled_both_ways(
        self, conformances: list[ConformanceObservation]
    ) -> None:
        # Without violating outputs a validator that accepts everything scores 100%, and
        # without conforming ones a validator that refuses everything does.
        assert sum(1 for row in conformances if row.should_conform) >= 6
        assert sum(1 for row in conformances if not row.should_conform) >= 6


class TestWhatTheGateReports:
    def test_a_failure_names_the_cases(self) -> None:
        result = citation_accuracy(
            [
                CitationObservation(name="good", genuine=True, verified=True),
                CitationObservation(name="bad", genuine=False, verified=True, ratio="0.991"),
            ]
        )

        assert not result.passed
        assert "bad" in result.describe()
        assert "accepted a fabrication" in result.describe()

    def test_a_pass_is_one_line(self) -> None:
        result = unit_integrity([UnitObservation(name="usd + gbp", compatible=False, raised=True)])

        assert result.passed
        assert "\n" not in result.describe()

    def test_the_detection_rate_is_reported_even_though_it_does_not_gate(
        self, injections: list[InjectionObservation]
    ) -> None:
        """Recorded because it is worth watching, not gating because gating on it would
        reward a scanner that flags every document."""
        detected = sum(1 for row in injections if row.detected)

        assert detected > 0
        assert injection_resistance(injections).passed

    def test_every_result_serialises(
        self,
        citations: list[CitationObservation],
        sources: list[SourceObservation],
        injections: list[InjectionObservation],
        units: list[UnitObservation],
        replays: list[ReplayObservation],
        completeness: list[CompletenessObservation],
        conformances: list[ConformanceObservation],
        containments: list[ContainmentObservation],
    ) -> None:
        # `evaluations.details` is JSONB, and Phase 3 writes these rows.
        results = evaluate_all(
            citations=citations,
            sources=sources,
            injections=injections,
            units=units,
            replays=replays,
            completeness=completeness,
            conformances=conformances,
            containments=containments,
        )

        for result in results:
            payload = result.as_dict()
            assert payload["metric"]
            assert Decimal(payload["value"]) >= 0
