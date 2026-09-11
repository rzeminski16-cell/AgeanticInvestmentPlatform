"""The matcher: a numeral is right only if it reads as a filed figure under the platform's
readings."""

from __future__ import annotations

from decimal import Decimal

from audit.scoring.facts import Observation, Truth
from audit.scoring.match import classify_all, summarise
from audit.scoring.numerals import extract_numerals


def _truth() -> Truth:
    def obs(concept: str, year: int, value: str) -> Observation:
        return Observation(
            concept=concept,
            tag="us-gaap:X",
            fiscal_year=year,
            end=f"{year}-06-30",
            value=Decimal(value),
            unit="USD",
            filed=f"{year}-07-30",
            form="10-K",
            accession="",
            original_value=None,
            original_filed=None,
        )

    observations = (
        obs("revenue", 2024, "245122000000"),
        obs("revenue", 2023, "211915000000"),
        obs("net_income", 2024, "88136000000"),
        obs("operating_cash_flow", 2024, "118548000000"),
        obs("capex", 2024, "44477000000"),
    )
    derived = {
        "revenue_growth": {"FY2024": str(Decimal("245122000000") / Decimal("211915000000") - 1)},
        "free_cash_flow": {"FY2024": str(Decimal("118548000000") - Decimal("44477000000"))},
        "net_margin": {"FY2024": str(Decimal("88136000000") / Decimal("245122000000"))},
    }
    return Truth(
        cik="789019",
        entity="MICROSOFT CORP",
        as_of="2026-09-11",
        artefact_sha256="x",
        observations=observations,
        derived=derived,
    )


def test_right_figures_match_at_the_note_precision() -> None:
    text = (
        "Revenue was $245.1 billion in fiscal 2024 and net income was $88.1 billion. "
        "Free cash flow reached $74,071 million (FY2024)."
    )
    result = summarise(classify_all(extract_numerals(text), _truth()))
    assert result["contradicted"] == 0
    assert result["matched"] == 3


def test_a_wrong_figure_with_its_period_is_contradicted() -> None:
    text = "Revenue was $265.0 billion in fiscal 2024."
    result = summarise(classify_all(extract_numerals(text), _truth()))
    assert result["contradicted"] == 1
    assert result["contradictions"][0]["basket_concept"] == "revenue"


def test_a_near_miss_is_approximate_not_contradicted() -> None:
    text = "Revenue was about $247 billion in FY2024."
    result = summarise(classify_all(extract_numerals(text), _truth()))
    assert result["approx"] == 1
    assert result["contradicted"] == 0


def test_a_percentage_reads_as_a_stored_ratio() -> None:
    text = "Revenue grew 15.7% in fiscal 2024; the net margin was 36.0% in FY2024."
    result = summarise(classify_all(extract_numerals(text), _truth()))
    assert result["contradicted"] == 0
    assert result["matched"] == 2


def test_a_price_is_outside_the_truth() -> None:
    text = "The share price closed at $412.34 and the P/E is 35x."
    kinds = summarise(classify_all(extract_numerals(text), _truth()))["by_kind"]
    assert kinds.get("outside-truth", 0) == 2
    assert "contradicted" not in kinds
