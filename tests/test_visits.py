"""Where Today's briefing starts (page specification §1, corrected 28 September 2026).

*Since {day}* is what happened while the operator was away. The rule has three parts, and the
one that matters most is the one a naive stamp gets wrong: a reload is not a new visit, so it
must not empty the briefing the operator has not finished reading — and nor may it claim a
look that never happened.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from aer.core.visits import FIRST_LOOK, VISIT_GAP, visit_of

NOW = datetime(2026, 9, 28, 9, 0, tzinfo=UTC)


class TestTheWindow:
    def test_a_first_visit_shows_the_last_seven_days(self) -> None:
        visit = visit_of(seen_at=None, looked_before=None, now=NOW)

        assert visit.is_first
        assert visit.since == NOW - FIRST_LOOK

    def test_the_morning_after_starts_from_the_last_look(self) -> None:
        last = NOW - timedelta(hours=15)

        visit = visit_of(seen_at=last, looked_before=last - timedelta(days=2), now=NOW)

        assert visit.since == last
        assert visit.last_looked == last

    def test_a_reload_keeps_the_visits_window(self) -> None:
        # Opened at 09:00, having last looked on Thursday; reloaded at 09:10. The briefing
        # still starts on Thursday, not ten minutes ago.
        thursday = NOW - timedelta(days=4)

        visit = visit_of(seen_at=NOW, looked_before=thursday, now=NOW + timedelta(minutes=10))

        assert visit.since == thursday
        assert visit.last_looked == thursday

    def test_a_reload_during_the_first_visit_claims_no_look(self) -> None:
        # The first visit's window starts a week back, and nobody looked then. A reload must
        # not turn that date into "you last looked on 21 September".
        visit = visit_of(seen_at=NOW, looked_before=None, now=NOW + timedelta(minutes=10))

        assert visit.is_first
        assert visit.last_looked is None

    def test_the_gap_is_where_a_visit_ends(self) -> None:
        last = NOW - VISIT_GAP

        visit = visit_of(seen_at=last, looked_before=None, now=NOW)

        assert not visit.is_first
        assert visit.since == last

    def test_a_naive_time_is_refused(self) -> None:
        with pytest.raises(ValueError, match="aware"):
            visit_of(seen_at=None, looked_before=None, now=datetime(2026, 9, 28, 9))  # noqa: DTZ001


# Hypothesis takes naive bounds and applies the zone itself, so every drawn moment is aware.
moments = st.datetimes(
    min_value=datetime(2026, 1, 1),  # noqa: DTZ001
    max_value=datetime(2027, 1, 1),  # noqa: DTZ001
    timezones=st.just(UTC),
)
gaps = st.timedeltas(min_value=timedelta(0), max_value=timedelta(days=30))


class TestItNeverRunsForwards:
    @settings(max_examples=200, deadline=None)
    @given(seen=moments, away=gaps, earlier=gaps)
    def test_the_briefing_never_starts_after_the_look_it_briefs(
        self, seen: datetime, away: timedelta, earlier: timedelta
    ) -> None:
        now = seen + away
        visit = visit_of(seen_at=seen, looked_before=seen - earlier, now=now)

        assert visit.since <= now
        assert visit.last_looked is None or visit.last_looked <= now
