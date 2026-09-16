"""A macro client that answers from a dict, for tests that drive the risk-free acquisition.

The archive's whole guarantee is "the series as it stood on that date", and a stub that
returns one dict at one vintage is that guarantee in miniature: every observation it hands
back is stamped with the vintage it was constructed for, and what it was asked is kept so a
test can assert the run asked at its own as-of date and for the documented series only.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from aer.sources.macro.client import MacroResponse
from aer.sources.macro.fred import MacroObservation
from aer.sources.macro.series import series_for

__all__ = ["StubMacroClient"]


class StubMacroClient:
    """Answers a series request from a dict, the way the archive would at one vintage."""

    def __init__(
        self,
        values: dict[date, str] | None = None,
        *,
        vintage: date,
        raises: Exception | None = None,
    ) -> None:
        self._values = values or {}
        self._vintage = vintage
        self._raises = raises
        self.asked: list[tuple[str, date]] = []

    async def fetch_series(self, key: str, *, as_of: date) -> MacroResponse:
        self.asked.append((key, as_of))
        if self._raises is not None:
            raise self._raises
        return MacroResponse(
            series=series_for(key),
            vintage=self._vintage,
            observations=tuple(
                MacroObservation(observed_on=period, vintage=self._vintage, value=Decimal(value))
                for period, value in sorted(self._values.items())
            ),
            is_archived=True,
            fetch=None,  # type: ignore[arg-type]  # not read by the service
        )
