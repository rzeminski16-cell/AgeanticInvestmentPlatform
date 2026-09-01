"""Formatting a date the same way on every machine this platform runs on.

**``%-d`` is not portable, and the report died on that.** The no-padding directives
(``%-d``, ``%-m``, and the rest) are a glibc extension: they render "2 January 2026" on
Linux and macOS, and on Windows ``strftime`` refuses the whole pattern with
``ValueError: Invalid format string``. The house style's default date format uses one, so
on Windows every report crashed — and crashed at the *end*, in the evaluation step, after
the drafting had been paid for. A live run lost £5.91 to it.

It had bitten once before, in :func:`aer.services.requests.archive`, and was fixed there by
writing the day out instead of asking for ``%-d``. That was the right local fix and the
wrong general one: the lesson stayed in that function while the *default house
style* went on carrying the directive that caused it.

So the expansion happens here, in code, before the C library ever sees the pattern. A
``%-d`` is computed by this module and substituted as digits; what reaches ``strftime`` has
no platform-specific directives left in it. ``%#d``, the Windows spelling of the same idea,
is accepted too, so a pattern an operator writes on either platform means the same thing on
both.

Pure and free of I/O, in ``core`` (``mypy --strict``), because both the renderer and the
settings validator need it and the settings module cannot import the renderer.
"""

from __future__ import annotations

from datetime import date

__all__ = ["fiscal_year_of", "format_date"]

# How far into January a period can end and still belong to the prior fiscal year. Seven
# days covers every Saturday-nearest-to-31-December convention a 52/53-week calendar can
# produce (the fourth of January is the furthest) without reaching the genuine mid-January
# year ends, which are their own calendar year's.
_JANUARY_GRACE_DAYS = 7


def fiscal_year_of(period_end: date) -> int:
    """The fiscal year a year-long period ending on ``period_end`` belongs to.

    The calendar year the period ends in — the convention the store has always used ("a
    year ending September 2025 is FY2025") — except that a period ending in the first
    seven days of January belongs to the prior year, because a 52/53-week calendar ending
    the Saturday nearest 31 December can land a few days into January, and a year that is
    in substance 2026 must not flip to FY2027 over two days (ADR 0062).

    A labelling convention applied uniformly, not a reproduction of each filer's own
    naming: what comparability needs is that one company's every figure sits under the
    same rule, which is exactly what the SEC's ``fy`` field — the *filing's* fiscal frame,
    stamped onto every comparative it carries — did not provide.
    """
    if period_end.month == 1 and period_end.day <= _JANUARY_GRACE_DAYS:
        return period_end.year - 1
    return period_end.year


# The directives whose padding the flag removes. Each is numeric, so the unpadded form is
# the padded one read as an integer — which is how they are computed below, rather than by
# reimplementing the calendar.
_UNPADDED: frozenset[str] = frozenset("dmHIjMSyUW")

# `-` is glibc's spelling of "no padding" and `#` is the Microsoft C runtime's. Accepting
# both is what makes a configured pattern portable in both directions: an operator who
# writes `%#d` on Windows gets the same output on Linux, and vice versa.
_FLAGS: frozenset[str] = frozenset("-#")


def format_date(value: date, pattern: str) -> str:
    """``value`` rendered by ``pattern``, with the no-padding directives handled here.

    Args:
        value: The date. ``datetime`` works too, being a subclass.
        pattern: A ``strftime`` pattern, which may use ``%-d``/``%#d`` style directives.

    Raises:
        ValueError: If what remains after expansion is not a pattern this platform's
            ``strftime`` accepts. A genuinely malformed pattern still fails — the point is
            that a *portable* one no longer does.
    """
    return value.strftime(_expanded(pattern, value))


def _expanded(pattern: str, value: date) -> str:
    """The pattern with every no-padding directive replaced by its digits.

    Written as a scan rather than a regular expression because of ``%%``: a literal percent
    followed by ``-d`` is the text "%-d", not a directive, and a substitution that matched
    it would silently rewrite a string the operator meant literally.
    """
    out: list[str] = []
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if char != "%" or index + 1 >= len(pattern):
            out.append(char)
            index += 1
            continue

        following = pattern[index + 1]
        if following == "%":
            # An escaped percent. Copied whole so the next character is never read as a
            # directive belonging to it.
            out.append("%%")
            index += 2
            continue

        if following in _FLAGS and index + 2 < len(pattern) and pattern[index + 2] in _UNPADDED:
            out.append(str(int(value.strftime(f"%{pattern[index + 2]}"))))
            index += 3
            continue

        out.append(pattern[index : index + 2])
        index += 2

    return "".join(out)
