"""The refusal-cause counter: every producer's wording, classified (polish P6).

The first complete run's failure causes had to be reconstructed by reading a 3,000-line
worker log. :func:`aer.sections.evidence.classify_refusals` is that reading done once, in
code, and every test here builds its problem through the *real* producer — the numeral
rule, the word budget, the claim checks, the schema report — so a producer that rewords
its refusal fails here rather than silently landing in the fallback bucket.
"""

from __future__ import annotations

from typing import Any

from aer.agents.base import TokenCapExceededError, schema_problems
from aer.agents.custom_section import CustomSectionDraft
from aer.errors import ValidationError
from aer.sections.evidence import Evidence, SectionPolicy, classify_refusals, validate_draft
from aer.sections.valuation_method import commentary_problems

CONTRACT: dict[str, Any] = {
    "type": "object",
    "required": ["commentary"],
    "properties": {
        "commentary": {"type": "string"},
        "figures": {"type": "array"},
    },
}


def policy(**overrides: Any) -> SectionPolicy:
    stated: dict[str, Any] = {
        "min_sources": 0,
        "requires_primary": False,
        "max_tier_rank": 5,
        "allow_forward_looking": False,
        "token_budget": 1000,
    }
    stated.update(overrides)
    return SectionPolicy(**stated)


def draft(content: dict[str, Any], claims: list[dict[str, Any]] | None = None) -> Any:
    return CustomSectionDraft(content=content, claims=claims or [])


def refused(content: dict[str, Any], *, claims: list[dict[str, Any]] | None = None, **p: Any):
    problems = validate_draft(
        draft(content, claims), contract=CONTRACT, evidence=Evidence(), policy=policy(**p)
    )
    assert problems, "the producer under test refused nothing"
    return problems


class TestEachProducerHasItsCause:
    """Each refusal, built by its real producer, lands in its own bucket."""

    def test_a_word_budget_overrun_is_length(self) -> None:
        wordy = " ".join(["analysis"] * 40)
        problems = refused({"commentary": wordy}, word_budget=10)

        assert classify_refusals(problems) == {"length": 1}

    def test_gap_dwelling_is_gaps(self) -> None:
        problems = refused(
            {
                "commentary": (
                    "Margins were not disclosed. Segment detail was not reported. "
                    "Headcount is unavailable."
                )
            }
        )

        assert classify_refusals(problems) == {"gaps": 1}

    def test_an_unsourced_numeral_is_numeral(self) -> None:
        problems = refused({"commentary": "Revenue grew 42 per cent."})

        assert classify_refusals(problems) == {"numeral": 1}

    def test_a_claim_on_evidence_the_call_was_not_shown_is_citation(self) -> None:
        claims = [
            {
                "statement": "A statement about the company.",
                "kind": "factual",
                "citations": [
                    {
                        "source_document_id": "11111111-1111-1111-1111-111111111111",
                        "extraction_id": "22222222-2222-2222-2222-222222222222",
                    }
                ],
            }
        ]
        problems = refused({"commentary": "Prose."}, claims=claims)

        assert classify_refusals(problems) == {"citation": 1}

    def test_a_content_id_outside_the_evidence_is_citation(self) -> None:
        """A figure row naming a calculation the call was never shown — the built-in
        convention's closed world, refused as a citation problem."""
        problems = refused(
            {
                "commentary": "Prose.",
                "figures": [
                    {
                        "label": "Revenue CAGR",
                        "value": "1",
                        "unit": "ratio",
                        "calculation_id": "33333333-3333-3333-3333-333333333333",
                    }
                ],
            }
        )

        assert classify_refusals(problems) == {"citation": 1}

    def test_a_forward_looking_claim_where_none_is_admitted_is_policy(self) -> None:
        claims = [
            {
                "statement": "Margins should widen from here.",
                "kind": "forward_looking",
                "basis": "Operating leverage on a fixed cost base.",
                "citations": [],
            }
        ]
        problems = refused({"commentary": "Prose."}, claims=claims)

        assert classify_refusals(problems) == {"policy": 1}

    def test_a_contract_violation_is_schema(self) -> None:
        problems = refused({"commentary": "Prose.", "surprise": "undeclared"})

        assert classify_refusals(problems) == {"schema": 1}

    def test_a_field_level_schema_report_is_schema(self) -> None:
        rejected = ValidationError(
            "claude-opus-5's reply could not be read as SectionDraft.",
            context={
                "errors": [{"loc": "('content', 'commentary')", "msg": "String should be shorter"}]
            },
        )

        assert classify_refusals(schema_problems(rejected)) == {"schema": 1}

    def test_a_reply_stopped_at_the_ceiling_is_truncation(self) -> None:
        rejected = ValidationError(
            "claude-opus-5 produced no SectionDraft: it ran out of room.",
            context={"stop_reason": "max_tokens"},
        )

        assert classify_refusals(schema_problems(rejected)) == {"truncation": 1}

    def test_a_cut_off_json_reply_is_truncation(self) -> None:
        rejected = ValidationError(
            "unreadable",
            context={"errors": [{"type": "json_invalid", "msg": "EOF while parsing"}]},
        )

        assert classify_refusals(schema_problems(rejected)) == {"truncation": 1}

    def test_a_composition_the_window_refuses_is_truncation(self) -> None:
        refusal = TokenCapExceededError(
            "The report_writer agent composed a call of 999,999 input tokens which, with "
            "its 16,384-token output ceiling, cannot fit claude-opus-5's 200,000-token "
            "context window. The call was refused before it was made; no smaller retry "
            "exists, so the composition itself has to shrink.",
            context={"role": "report_writer"},
        )

        assert classify_refusals([str(refusal)]) == {"truncation": 1}

    def test_a_method_claim_the_record_lacks_is_method(self) -> None:
        """Two refusals from one sentence — the return series, and the absent beta —
        and both land in the method bucket."""
        problems = commentary_problems(
            {"commentary": "Beta was estimated from five years of weekly returns."}, {}
        )

        assert classify_refusals(problems) == {"method": 2}

    def test_causes_are_counted_not_listed(self) -> None:
        wordy = " ".join(["analysis"] * 40) + " It grew 42 per cent."
        problems = refused({"commentary": wordy}, word_budget=10)

        counted = classify_refusals(problems)
        assert counted["length"] == 1
        assert counted["numeral"] == 1

    def test_nothing_refused_counts_nothing(self) -> None:
        assert classify_refusals([]) == {}
