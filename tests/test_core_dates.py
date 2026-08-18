"""Dates format the same way on every platform, and the default is one of them.

A live run died at `ValueError: Invalid format string` on Windows, in the evaluation step,
after £5.91 of drafting had been paid for. The cause was the house style's default date
format: `%-d` is a glibc extension, and the Microsoft C runtime refuses the whole pattern.

The tests that matter here are the ones that would still fail on Linux if the fix were
wrong — asserting the *expansion*, not just the output — because a test that only checks
"2 January 2026" comes out passes on the platform that never had the bug.
"""

from __future__ import annotations

import re
from datetime import date, datetime

import pytest

from aer.config import HouseStyle
from aer.core.dates import _expanded as expanded
from aer.core.dates import format_date

WHEN = date(2026, 1, 2)


class TestTheDirectivesWindowsRefuses:
    """`%-d` and friends are expanded here, so `strftime` never sees one."""

    def test_the_day_loses_its_padding(self) -> None:
        assert format_date(WHEN, "%-d %B %Y") == "2 January 2026"

    def test_the_windows_spelling_means_the_same_thing(self) -> None:
        """An operator who writes `%#d` on Windows gets the same report on Linux."""
        assert format_date(WHEN, "%#d %B %Y") == format_date(WHEN, "%-d %B %Y")

    @pytest.mark.parametrize("directive", ["d", "m", "H", "I", "j", "M", "S", "y"])
    def test_no_platform_specific_directive_survives_expansion(self, directive: str) -> None:
        """The property the fix rests on, checkable on the platform that works.

        Asserting the rendered text would prove nothing here: glibc renders `%-d`
        correctly, so a broken implementation passes on Linux and fails on the operator's
        machine — which is the whole history of this bug.
        """
        assert "%" not in expanded(f"%-{directive}", WHEN)
        assert "%" not in expanded(f"%#{directive}", WHEN)

    def test_the_padded_forms_are_left_to_strftime(self) -> None:
        assert expanded("%d %B %Y", WHEN) == "%d %B %Y"
        assert format_date(WHEN, "%d %B %Y") == "02 January 2026"

    def test_a_datetime_formats_too(self) -> None:
        moment = datetime(2026, 1, 2, 9, 5, tzinfo=None)  # noqa: DTZ001 -- formatting, not a clock
        assert format_date(moment, "%-H:%-M") == "9:5"


class TestUnderALibraryThatRefusesTheFlag:
    """The live failure, reproduced on the platform that does not have it.

    `_strict` is the Microsoft C runtime's behaviour in one line: any pattern still
    carrying `%-` or `%#` is refused outright. Every other test here would pass against
    a fix that did nothing on glibc; this one would not.
    """

    @staticmethod
    def _strict(value: date, pattern: str) -> str:
        if re.search(r"%[-#]", pattern):
            message = "Invalid format string"
            raise ValueError(message)
        return value.strftime(pattern)

    def test_the_unfixed_call_is_what_killed_the_run(self) -> None:
        with pytest.raises(ValueError, match="Invalid format string"):
            self._strict(WHEN, HouseStyle().date_format)

    def test_the_expanded_pattern_is_accepted(self) -> None:
        assert self._strict(WHEN, expanded(HouseStyle().date_format, WHEN)) == "2 January 2026"

    @pytest.mark.parametrize(
        ("pattern", "expected"),
        [
            ("%-d %B %Y", "2 January 2026"),
            ("%#d/%#m/%Y", "2/1/2026"),
            ("100%% of %-d", "100% of 2"),
            ("%d %B %Y", "02 January 2026"),
        ],
    )
    def test_every_supported_pattern_survives_it(self, pattern: str, expected: str) -> None:
        assert self._strict(WHEN, expanded(pattern, WHEN)) == expected


class TestAnEscapedPercentIsNotADirective:
    """The reason this is a scan rather than a regular expression."""

    def test_a_literal_percent_followed_by_a_flag_is_text(self) -> None:
        assert format_date(WHEN, "100%% of %-d") == "100% of 2"

    def test_the_escape_does_not_swallow_the_next_directive(self) -> None:
        assert format_date(WHEN, "%%%-d") == "%2"

    def test_a_trailing_percent_is_left_alone(self) -> None:
        # Passed through for strftime to judge rather than guessed at here; what matters
        # is that the scanner does not read past the end of the string.
        assert expanded("%B %", WHEN) == "%B %"


class TestTheHouseStyleDefault:
    """The value that broke, checked where it is declared."""

    def test_the_default_renders_a_uk_date_without_padding(self) -> None:
        assert format_date(WHEN, HouseStyle().date_format) == "2 January 2026"

    def test_the_default_is_validated_rather_than_trusted(self) -> None:
        """Pydantic skips validators on defaults unless asked, and that is how it shipped.

        Asserted through the model rather than by reading the field: what went wrong was
        that the check existed and never ran.
        """
        assert HouseStyle.model_fields["date_format"].validate_default is True

    def test_a_pattern_that_ignores_the_date_is_refused(self) -> None:
        with pytest.raises(ValueError, match="does not render the date"):
            HouseStyle(date_format="Quarterly report")

    def test_an_unknown_directive_is_still_refused(self) -> None:
        """Portability is not permissiveness.

        Which arm of the validator catches it is platform-dependent and deliberately not
        asserted: glibc renders an unknown `%-Q` as the literal text "%-Q", so the
        behavioural check refuses it, while a stricter library raises and the other arm
        does. What must hold everywhere is that it does not reach a report.
        """
        with pytest.raises(ValueError, match="date_format"):
            HouseStyle(date_format="%-Q")
