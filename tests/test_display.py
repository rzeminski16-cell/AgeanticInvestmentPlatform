"""The display formatter: raw stored values re-said in the house style, at render only.

Gap R1. The live report printed ``11729000000 USD`` mid-sentence and ``0.462`` where a
reader needed ``46.2%``. These tests pin the one formatter that now sits between stored
values and the page — and its refusals, because a number whose meaning nothing states
must pass through unreinterpreted.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from aer.config import HouseStyle
from aer.render import display

STYLE = HouseStyle()
TIMES = "\N{MULTIPLICATION SIGN}"


class TestMoney:
    def test_prose_scales_to_billions_above_the_threshold(self) -> None:
        assert display.money(Decimal("391035000000"), "USD", style=STYLE) == "$391.0bn"

    def test_prose_below_the_threshold_stays_in_millions(self) -> None:
        assert display.money(Decimal("245122000"), "USD", style=STYLE) == "$245m"

    def test_a_table_never_scales_past_millions(self) -> None:
        """A column only lines up in one scale."""
        formatted = display.money(Decimal("391035000000"), "USD", style=STYLE, in_table=True)
        assert formatted == "$391,035m"

    def test_the_millions_style_never_reaches_billions(self) -> None:
        style = HouseStyle(prose_money="millions")
        assert display.money(Decimal("391035000000"), "USD", style=style) == "$391,035m"

    def test_the_threshold_is_configuration(self) -> None:
        style = HouseStyle(billions_from=Decimal("10000000000"))
        assert display.money(Decimal("9000000000"), "USD", style=style) == "$9,000m"
        assert display.money(Decimal("11000000000"), "USD", style=style) == "$11.0bn"

    def test_below_a_million_nothing_is_scaled(self) -> None:
        """Pretending $250,000 is "$0m" is not presentation."""
        assert display.money(Decimal("250000"), "USD", style=STYLE) == "$250,000"

    def test_sterling_and_an_unmapped_currency(self) -> None:
        assert display.money(Decimal("2000000000"), "GBP", style=STYLE) == "£2.0bn"
        assert display.money(Decimal("1234500000"), "SEK", style=STYLE) == "SEK 1.2bn"


class TestScalar:
    def test_a_per_share_amount_keeps_its_pence(self) -> None:
        assert display.scalar("123.456", style=STYLE, unit="USD/shares") == "$123.46"

    def test_a_margin_label_reads_as_a_percentage(self) -> None:
        assert display.scalar("0.462", style=STYLE, unit="pure", label="Operating margin") == (
            "46.2%"
        )

    def test_a_ratio_label_reads_as_times(self) -> None:
        formatted = display.scalar("0.495", style=STYLE, unit="pure", label="Current ratio")
        assert formatted == f"0.5{TIMES}"

    def test_an_unlabelled_pure_number_is_nobody_s_to_reinterpret(self) -> None:
        assert display.scalar("0.462", style=STYLE, unit="pure") == "0.462"

    def test_a_share_count_gains_separators_and_nothing_else(self) -> None:
        assert display.scalar("15408095000", style=STYLE, unit="shares") == "15,408,095,000"

    def test_a_currency_value_in_a_table_cell(self) -> None:
        item = {"label": "Revenue", "value": "109417000000", "unit": "USD"}
        assert display.cell(item, "value", style=STYLE) == "$109,417m"

    def test_a_non_numeric_string_passes_through(self) -> None:
        assert display.scalar("improving", style=STYLE, unit="pure") == "improving"


class TestProse:
    def test_a_longhand_amount_against_a_code_is_re_said(self) -> None:
        text = "research and development was 11729000000 USD in the quarter"
        assert (
            display.prose(text, style=STYLE)
            == "research and development was $11.7bn in the quarter"
        )

    def test_an_iso_date_is_re_said(self) -> None:
        assert display.prose("filed on 2025-10-30.", style=STYLE) == "filed on 30 October 2025."

    def test_a_small_amount_is_left_as_written(self) -> None:
        """Rescaling "45 USD per share" buys nothing and edits a sentence."""
        text = "a dividend of 45 USD per share"
        assert display.prose(text, style=STYLE) == text

    def test_a_bare_number_is_never_touched(self) -> None:
        text = "three segments grew 0.462 of the time in 2025"
        assert display.prose(text, style=STYLE) == text


class TestDates:
    def test_the_default_style_is_uk_prose(self) -> None:
        assert display.date_text(date(2026, 8, 17), style=STYLE) == "17 August 2026"

    def test_the_format_is_configuration(self) -> None:
        style = HouseStyle(date_format="%Y-%m-%d")
        assert display.date_text(date(2026, 8, 17), style=style) == "2026-08-17"
