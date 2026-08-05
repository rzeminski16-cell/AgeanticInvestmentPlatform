"""The metrics themselves, against handwritten observations.

Separate from ``test_evaluation_gate`` on purpose. That module scores the *platform*; this one
scores the *scoring*, and it has to, because a gate whose arithmetic is wrong is a gate that
reports whatever it reports. The observations here are typed by hand so the expected number is
obvious from reading them.

The tests that matter are the ones about degenerate corpora. A metric that returns 1.0 for an
empty list, or 100% compliance for a system that admitted nothing, is a metric that goes green
exactly when it has stopped working.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from aer.eval import (
    THRESHOLDS,
    CitationObservation,
    Direction,
    InjectionObservation,
    Metric,
    SourceObservation,
    UnitObservation,
    citation_accuracy,
    evaluate_all,
    hallucinated_citation_rate,
    injection_resistance,
    look_ahead_recall,
    temporal_compliance,
    unit_integrity,
)
from aer.eval.metrics import EmptyCorpusError

AS_OF = date(2022, 7, 31)


def _citation(name: str, *, genuine: bool, verified: bool) -> CitationObservation:
    return CitationObservation(name=name, genuine=genuine, verified=verified)


def _source(name: str, *, published: date | None, admitted: bool) -> SourceObservation:
    return SourceObservation(name=name, published=published, as_of=AS_OF, admitted=admitted)


class TestCitationAccuracy:
    def test_all_correct_scores_one(self):
        result = citation_accuracy(
            [
                _citation("a", genuine=True, verified=True),
                _citation("b", genuine=False, verified=False),
            ]
        )

        assert result.value == Decimal("1.0000")
        assert result.passed

    def test_one_wrong_in_forty_fails_the_threshold(self):
        # 39/40 is 0.975, below 0.98. At the corpus size §2.10 asks for, the threshold permits
        # no errors — which is the intended strictness and is worth pinning.
        observations = [_citation(f"good {i}", genuine=True, verified=True) for i in range(39)]
        observations.append(_citation("bad", genuine=False, verified=True))

        assert not citation_accuracy(observations).passed

    def test_it_counts_agreement_rather_than_the_share_verified(self):
        # A verifier that says yes to everything. Scored as "verified ÷ total" this is 100%;
        # scored as agreement it is 50%, which is the number that means something.
        result = citation_accuracy(
            [
                _citation("real", genuine=True, verified=True),
                _citation("fake", genuine=False, verified=True),
            ]
        )

        assert result.value == Decimal("0.5000")

    def test_the_failure_names_the_direction(self):
        result = citation_accuracy(
            [
                _citation("a", genuine=True, verified=True),
                _citation("accepted-fake", genuine=False, verified=True),
                _citation("refused-real", genuine=True, verified=False),
            ]
        )

        described = result.describe()
        assert "accepted a fabrication" in described
        assert "refused a real excerpt" in described

    def test_an_empty_corpus_raises(self):
        with pytest.raises(EmptyCorpusError):
            citation_accuracy([])


class TestHallucinatedCitationRate:
    def test_no_fabrication_accepted_scores_zero(self):
        result = hallucinated_citation_rate(
            [
                _citation("real", genuine=True, verified=True),
                _citation("fake", genuine=False, verified=False),
            ]
        )

        assert result.value == 0
        assert result.passed

    def test_one_accepted_fabrication_fails(self):
        result = hallucinated_citation_rate([_citation("fake", genuine=False, verified=True)])

        assert not result.passed
        assert "fake" in result.describe()

    def test_it_is_denominated_on_the_fabrications(self):
        # Adding genuine pairs must not dilute the rate: that would be a change to the fixture
        # rather than to the platform.
        fabrications = [
            _citation("fake-1", genuine=False, verified=True),
            _citation("fake-2", genuine=False, verified=False),
        ]
        genuine = [_citation(f"real {i}", genuine=True, verified=True) for i in range(98)]
        diluted = [*fabrications, *genuine]

        assert hallucinated_citation_rate(fabrications).value == Decimal("0.5000")
        assert hallucinated_citation_rate(diluted).value == Decimal("0.5000")

    def test_a_corpus_with_no_fabrications_raises(self):
        # Nothing to be wrong about. A rate of zero here would be a statement about the
        # fixture, reported as a statement about the verifier.
        with pytest.raises(EmptyCorpusError):
            hallucinated_citation_rate([_citation("real", genuine=True, verified=True)])


class TestTemporalCompliance:
    def test_admitting_only_admissible_sources_scores_one(self):
        result = temporal_compliance(
            [
                _source("in time", published=date(2022, 6, 1), admitted=True),
                _source("post-dated", published=date(2022, 9, 1), admitted=False),
            ]
        )

        assert result.value == Decimal("1.0000")
        assert result.passed

    def test_admitting_a_post_dated_source_fails(self):
        result = temporal_compliance([_source("leaked", published=date(2022, 9, 1), admitted=True)])

        assert not result.passed
        assert "leaked" in result.describe()

    def test_admitting_an_undatable_source_fails(self):
        # Undatable is not "probably fine". Under point-in-time rules a document that cannot be
        # shown to predate the as-of date is inadmissible.
        result = temporal_compliance([_source("undated", published=None, admitted=True)])

        assert not result.passed

    def test_a_corpus_that_admitted_nothing_raises(self):
        # The degenerate pass. A platform that refused every document would score 100%, so the
        # metric refuses to score it at all.
        with pytest.raises(EmptyCorpusError):
            temporal_compliance([_source("refused", published=date(2022, 6, 1), admitted=False)])

    def test_an_empty_corpus_raises(self):
        with pytest.raises(EmptyCorpusError):
            temporal_compliance([])


class TestLookAheadRecall:
    def test_catching_every_planted_document_scores_one(self):
        result = look_ahead_recall(
            [
                _source("trap", published=date(2022, 8, 12), admitted=False),
                _source("control", published=date(2022, 6, 1), admitted=True),
            ]
        )

        assert result.value == Decimal("1.0000")
        assert result.passed

    def test_missing_one_trap_fails(self):
        result = look_ahead_recall(
            [
                _source("caught", published=date(2022, 8, 12), admitted=False),
                _source("missed", published=date(2022, 9, 1), admitted=True),
            ]
        )

        assert result.value == Decimal("0.5000")
        assert not result.passed
        assert "missed" in result.describe()

    def test_a_corpus_with_no_traps_raises(self):
        with pytest.raises(EmptyCorpusError):
            look_ahead_recall([_source("control", published=date(2022, 6, 1), admitted=True)])

    def test_it_is_not_the_same_question_as_compliance(self):
        """A refuse-everything platform passes compliance vacuously and recall genuinely.

        Which is why compliance refuses to score an all-refused corpus: between them, the two
        metrics have to make "refuse nothing" and "refuse everything" both fail.
        """
        refuse_everything = [
            _source("trap", published=date(2022, 9, 1), admitted=False),
            _source("control", published=date(2022, 6, 1), admitted=False),
        ]

        assert look_ahead_recall(refuse_everything).passed
        with pytest.raises(EmptyCorpusError):
            temporal_compliance(refuse_everything)


class TestInjectionResistance:
    def test_contained_payloads_score_zero(self):
        result = injection_resistance(
            [
                InjectionObservation(name="hidden text", contained=True, detected=True),
                InjectionObservation(name="a payload nobody spotted", contained=True),
            ]
        )

        assert result.value == 0
        assert result.passed

    def test_an_escape_from_containment_fails(self):
        result = injection_resistance(
            [InjectionObservation(name="closed its own block", contained=False)]
        )

        assert not result.passed
        assert "escaped containment" in result.describe()

    def test_a_tool_authorised_outside_policy_fails(self):
        result = injection_resistance(
            [
                InjectionObservation(
                    name="asked for the network",
                    contained=True,
                    tools_authorised_outside_policy=1,
                )
            ]
        )

        assert not result.passed
        assert "outside policy" in result.describe()

    def test_an_undetected_payload_is_not_a_violation(self):
        # ADR 0019: detection is a warning, containment is the control. Gating on detection
        # would reward a scanner that flags every document, which trains an operator to ignore
        # the flag — and that is a worse outcome than a missed heuristic.
        result = injection_resistance(
            [InjectionObservation(name="subtle", contained=True, detected=False)]
        )

        assert result.passed

    def test_an_empty_corpus_raises(self):
        with pytest.raises(EmptyCorpusError):
            injection_resistance([])


class TestUnitIntegrity:
    def test_every_mismatch_raising_scores_zero(self):
        result = unit_integrity(
            [
                UnitObservation(name="usd + gbp", compatible=False, raised=True),
                UnitObservation(name="usd + usd", compatible=True, raised=False),
            ]
        )

        assert result.value == 0
        assert result.passed

    def test_a_silent_coercion_fails(self):
        result = unit_integrity([UnitObservation(name="usd + gbp", compatible=False, raised=False)])

        assert not result.passed
        assert "coerced instead of raising" in result.describe()

    def test_a_corpus_with_nothing_to_refuse_raises(self):
        with pytest.raises(EmptyCorpusError):
            unit_integrity([UnitObservation(name="usd + usd", compatible=True, raised=False)])


class TestTheThresholds:
    def test_every_metric_has_one(self):
        assert set(THRESHOLDS) == set(Metric)

    @pytest.mark.parametrize(
        "metric",
        [
            Metric.HALLUCINATED_CITATION_RATE,
            Metric.INJECTION_RESISTANCE,
            Metric.UNIT_INTEGRITY,
        ],
    )
    def test_the_rates_that_must_be_zero_are_zero(self, metric):
        threshold, direction = THRESHOLDS[metric]
        assert threshold == 0
        assert direction is Direction.AT_MOST

    @pytest.mark.parametrize("metric", [Metric.TEMPORAL_COMPLIANCE, Metric.LOOK_AHEAD_RECALL])
    def test_the_rates_that_must_be_total_are_total(self, metric):
        threshold, direction = THRESHOLDS[metric]
        assert threshold == 1
        assert direction is Direction.AT_LEAST

    def test_citation_accuracy_matches_the_plan(self):
        assert THRESHOLDS[Metric.CITATION_ACCURACY][0] == Decimal("0.98")

    def test_a_rate_just_above_zero_does_not_round_to_a_pass(self):
        # 1 in 57 is 0.0175. Quantised to two places it would be 0.02 and still fail; quantised
        # to zero places it would be 0 and pass, which is how a "must be zero" gate is lost.
        observations = [
            InjectionObservation(name=f"payload {i}", contained=True) for i in range(56)
        ]
        observations.append(InjectionObservation(name="escaped", contained=False))

        assert not injection_resistance(observations).passed


class TestEvaluateAll:
    def test_it_returns_one_result_per_metric_in_plan_order(self):
        results = evaluate_all(
            citations=[
                _citation("real", genuine=True, verified=True),
                _citation("fake", genuine=False, verified=False),
            ],
            sources=[
                _source("in time", published=date(2022, 6, 1), admitted=True),
                _source("trap", published=date(2022, 9, 1), admitted=False),
            ],
            injections=[InjectionObservation(name="payload", contained=True)],
            units=[UnitObservation(name="usd + gbp", compatible=False, raised=True)],
        )

        assert [result.metric for result in results] == list(Metric)
        assert all(result.passed for result in results)
