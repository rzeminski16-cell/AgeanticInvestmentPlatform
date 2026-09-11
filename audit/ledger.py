"""The audit's own spending ceiling, across the platform and the baseline.

The platform meters its own calls into the ``costs`` table and caps them in code
(invariant 6). The baseline is not a platform call and writes no row there, so the audit's
£100 ceiling has to be held by something that sees both: this. It reads the platform's total
from the table and the baseline's from its own file, and refuses to start a paid stage that
would take the sum past the ceiling.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.db.models import Cost
from aer.errors import AerError

__all__ = ["CEILING_GBP", "Ledger", "LedgerExceededError"]

CEILING_GBP: Final = Decimal("100.00")
OUT_DIR: Final = Path("audit/out")
LEDGER_FILE: Final = OUT_DIR / "ledger.json"


class LedgerExceededError(AerError):
    """A paid stage would take the audit past its ceiling."""

    code = "audit_ledger_exceeded"


@dataclass(frozen=True, slots=True)
class LedgerReading:
    platform_gbp: Decimal
    baseline_gbp: Decimal
    other_gbp: Decimal

    @property
    def total_gbp(self) -> Decimal:
        return self.platform_gbp + self.baseline_gbp + self.other_gbp

    @property
    def remaining_gbp(self) -> Decimal:
        return CEILING_GBP - self.total_gbp

    def as_dict(self) -> dict[str, str]:
        return {
            "platform_gbp": str(self.platform_gbp),
            "baseline_gbp": str(self.baseline_gbp),
            "other_gbp": str(self.other_gbp),
            "total_gbp": str(self.total_gbp),
            "ceiling_gbp": str(CEILING_GBP),
            "remaining_gbp": str(self.remaining_gbp),
        }


class Ledger:
    """Reads both halves of the spend; records the half the platform cannot see."""

    def __init__(self, path: Path = LEDGER_FILE) -> None:
        self._path = path

    def _file(self) -> dict[str, Any]:
        if not self._path.exists():
            return {"baseline": [], "other": []}
        loaded: dict[str, Any] = json.loads(self._path.read_text(encoding="utf-8"))
        return loaded

    def _write(self, data: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(data, indent=2, default=str) + "\n", encoding="utf-8")

    async def platform_spend(self, session: AsyncSession) -> Decimal:
        """Every pound the platform metered, whatever run it belonged to."""
        total = await session.scalar(select(func.coalesce(func.sum(Cost.amount_gbp), 0)))
        return Decimal(str(total or 0))

    def record(self, *, kind: str, label: str, amount_gbp: Decimal, detail: dict[str, Any]) -> None:
        """Append one non-platform spend line (a baseline run, a judged scoring pass)."""
        if kind not in {"baseline", "other"}:
            message = f"A ledger line is 'baseline' or 'other', not {kind!r}."
            raise ValueError(message)
        data = self._file()
        data[kind].append(
            {
                "label": label,
                "amount_gbp": str(amount_gbp),
                "recorded_at": datetime.now(UTC).isoformat(),
                "detail": detail,
            }
        )
        self._write(data)

    def _sum(self, kind: str) -> Decimal:
        return sum((Decimal(line["amount_gbp"]) for line in self._file()[kind]), Decimal(0))

    async def reading(self, session: AsyncSession) -> LedgerReading:
        return LedgerReading(
            platform_gbp=await self.platform_spend(session),
            baseline_gbp=self._sum("baseline"),
            other_gbp=self._sum("other"),
        )

    async def assert_room(
        self, session: AsyncSession, *, stage: str, needs_gbp: Decimal
    ) -> LedgerReading:
        """Refuse to start ``stage`` unless ``needs_gbp`` fits under the ceiling."""
        reading = await self.reading(session)
        if reading.total_gbp + needs_gbp > CEILING_GBP:
            message = (
                f"{stage} would need £{needs_gbp} with £{reading.total_gbp} already spent "
                f"against a £{CEILING_GBP} ceiling; refusing to start it."
            )
            raise LedgerExceededError(message, context={"stage": stage, **reading.as_dict()})
        return reading
