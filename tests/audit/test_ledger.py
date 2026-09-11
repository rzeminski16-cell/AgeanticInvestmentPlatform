"""The audit's own ceiling: it must see both halves of the spend and refuse past £100."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from audit.ledger import CEILING_GBP, Ledger, LedgerExceededError


class _Session:
    """A session that answers the one query the ledger makes."""

    def __init__(self, platform_gbp: Decimal) -> None:
        self._platform = platform_gbp

    async def scalar(self, _query):
        return self._platform


async def test_the_reading_sums_platform_and_baseline(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.json")
    ledger.record(kind="baseline", label="msft", amount_gbp=Decimal("2.50"), detail={})
    ledger.record(kind="other", label="judges", amount_gbp=Decimal("1.00"), detail={})
    reading = await ledger.reading(_Session(Decimal("40.00")))
    assert reading.total_gbp == Decimal("43.50")
    assert reading.remaining_gbp == CEILING_GBP - Decimal("43.50")


async def test_a_stage_that_would_breach_is_refused(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.json")
    with pytest.raises(LedgerExceededError):
        await ledger.assert_room(_Session(Decimal("95.00")), stage="run", needs_gbp=Decimal("12"))


async def test_a_stage_that_fits_is_allowed(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.json")
    reading = await ledger.assert_room(
        _Session(Decimal("50.00")), stage="run", needs_gbp=Decimal("12")
    )
    assert reading.platform_gbp == Decimal("50.00")


def test_an_unknown_kind_is_refused(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.json")
    with pytest.raises(ValueError, match="baseline"):
        ledger.record(kind="platform", label="x", amount_gbp=Decimal(1), detail={})
