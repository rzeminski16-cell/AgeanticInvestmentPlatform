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


def test_a_half_year_figure_is_not_judged_against_the_year() -> None:
    """The M&T console note bridged "FY25 EPS $17.00 less 1H25 $7.55 plus 1H26 $9.44" to a
    trailing figure. Judging either half against the filed annual earnings per share
    manufactured a contradiction out of correct arithmetic, so a half-year or
    trailing-twelve-month qualifier sets the numeral aside like any other qualified
    measure."""
    text = (
        "Trailing EPS of $18.89 (computed from FY25 EPS $17.00 less 1H25 $7.55 plus "
        "1H26 $9.44); a screen reports TTM EPS $18.94."
    )
    result = summarise(classify_all(extract_numerals(text), _truth()))
    assert result["contradicted"] == 0


def test_a_row_label_naming_a_change_is_not_a_level() -> None:
    """M&T's report tabulates "Share repurchases change, FY2025 | 2.235 USD billions" and
    "Dividends paid change, FY2025 | 4 USD millions". Both read as their first concept to a
    phrase matcher, and both were judged against the level — a £2.235bn movement against a
    £2.631bn balance."""
    text = (
        "| Metric | Value |\n|---|---|\n"
        "| Share repurchases change, FY2024 | 2.235 USD billions |\n"
        "| Dividends paid change, FY2024 | 4 USD millions |\n"
    )
    result = summarise(classify_all(extract_numerals(text), _truth()))
    assert result["contradicted"] == 0


def test_a_row_label_naming_a_ratio_is_not_either_of_its_parts() -> None:
    """ "Distributions to operating cash flow" is a ratio of 1.18, not an operating cash
    flow of $1.18 — and the label contains the words of the line it is measured against."""
    text = (
        "| Metric | Value |\n|---|---|\n| Distributions to operating cash flow, FY2024 | 1.18 |\n"
    )
    result = summarise(classify_all(extract_numerals(text), _truth()))
    assert result["contradicted"] == 0


def test_a_period_stated_after_a_figure_belongs_to_that_figure() -> None:
    """ "9.8 percent for FY2025 against 8.9 percent for FY2024" states two years in one
    sentence; the prior year's figure was judged against the latest year's."""
    numerals = extract_numerals(
        "Revenue was $245.1 billion for FY2024 against $211.9 billion for FY2023."
    )
    periods = {str(n.value): n.period for n in numerals}
    assert periods["245.1"] == "FY2024"
    assert periods["211.9"] == "FY2023"
    result = summarise(classify_all(numerals, _truth()))
    assert result["contradicted"] == 0
    assert result["matched"] == 2


def test_a_figure_quoted_in_order_to_refuse_it_is_not_a_claim() -> None:
    """The withheld front page states the impossible relation it found, and the validation
    section quotes each failed check. On M&T the absurd margin appears nowhere else in the
    document, so reading the refusal as a claim judged the platform's own honesty."""
    text = (
        "The at-a-glance block was withheld — the figures offered to it cannot all be true "
        "at once: net margin 1.720579360290 for FY2024 is above 1 — income exceeding the "
        "revenue it is measured against."
    )
    result = summarise(classify_all(extract_numerals(text), _truth()))
    assert result["contradicted"] == 0


def test_a_concept_phrase_does_not_reach_across_another_figure() -> None:
    """The second AstraZeneca report stated pairs of figures in one sentence — operating
    cash flow against net income, R&D against revenue, R&D against SG&A — and each first
    figure was read as claiming the second's concept. A phrase separated from a numeral by
    another numeral attributes to neither, and a phrase with its own figure behind it
    belongs to that figure."""
    numerals = extract_numerals(
        "Operating cash flow was $118,548m against net income of $88,136m, and FY2024 "
        "revenue of $245,122m against capex of $44,477m."
    )
    by_value = {str(n.value): n.concept for n in numerals}
    assert by_value["118548"] == "operating_cash_flow"
    assert by_value["88136"] == "net_income"
    assert by_value["245122"] == "revenue"
    assert by_value["44477"] == "capex"
    assert summarise(classify_all(numerals, _truth()))["contradicted"] == 0


def test_a_parenthesised_year_after_a_figure_is_its_period() -> None:
    """ "capex rises from $1,361m (2023) to $1,924m (2024)" gives each figure the year in
    the brackets behind it, not the year the sentence opened with."""
    numerals = extract_numerals(
        "Revenue rises from $211,915m (2023) to $245,122m (2024) on the same basis."
    )
    periods = {str(n.value): n.period for n in numerals if n.excluded is None}
    assert periods["211915"] == "FY2023"
    assert periods["245122"] == "FY2024"


def test_a_phrase_right_behind_a_figure_labels_it() -> None:
    """ "a 67.9% gross margin, a 46.8% operating margin" labels each figure with the words
    right behind it. Refusing a forward reading whenever another figure followed the phrase
    sent the second margin back to the first's concept, so adjacency wins outright."""
    numerals = extract_numerals(
        "For FY2024 the company earned a 67.9% gross margin, a 46.8% operating margin and a "
        "36.0% net margin."
    )
    by_value = {str(n.value): n.concept for n in numerals}
    assert by_value["67.9"] == "gross_margin"
    assert by_value["46.8"] == "operating_margin"
    assert by_value["36.0"] == "net_margin"


def test_a_comma_separates_a_figure_from_the_next_item_s_label() -> None:
    """Both orders appear in the same reports: "a 67.9% gross margin, a 46.8% operating
    margin" labels each figure behind it, and "gross margin of 67.9%, operating margin of
    46.8%" labels each in front. The comma is what tells them apart, and without it every
    figure in the second shape took the next one's name."""
    labelled_behind = extract_numerals(
        "For FY2024 the company earned a 67.9% gross margin and a 36.0% net margin."
    )
    labelled_in_front = extract_numerals(
        "Fiscal 2024: revenue of $245,122m, gross margin of 67.9%, net margin of 36.0%."
    )
    for numerals in (labelled_behind, labelled_in_front):
        by_value = {str(n.value): n.concept for n in numerals if n.excluded is None}
        assert by_value["67.9"] == "gross_margin"
        assert by_value["36.0"] == "net_margin"
