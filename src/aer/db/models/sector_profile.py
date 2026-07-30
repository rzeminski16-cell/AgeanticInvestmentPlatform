"""What a sector permits, as a row rather than as a branch in code.

One row per sector, holding the allowed and blocked valuation models, the metrics a report
on that sector must carry, and the warnings it must state. Seeded in migration 0014 so
Phase 3 opens on data.

**A table rather than a match statement**, because the list grows by editing rows: the next
specialist sector somebody hits should be a seed change and a test, not a code change and a
deployment. The vocabulary is still typed — :mod:`aer.core.sectors` owns it, and a test
asserts the seeded rows and the constants there still agree.
"""

from __future__ import annotations

from sqlalchemy import CheckConstraint, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from aer.db.base import Base, created_at_column
from aer.db.types import JsonList, Timestamp, UuidPk

__all__ = ["SectorProfile"]


class SectorProfile(Base):
    __tablename__ = "sector_profiles"

    id: Mapped[UuidPk]

    # The stable identifier code refers to: "banks", "reits". Unique, because a duplicate
    # would make "which profile applies?" depend on row order.
    key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    label: Mapped[str] = mapped_column(Text, nullable=False)

    # Classification hints, deliberately coarse. The classifier is an agent whose proposal
    # a human confirms at the sector gate; these narrow the guess, they do not make it.
    sic_prefixes: Mapped[JsonList] = mapped_column(nullable=False, default=list)
    icb_codes: Mapped[JsonList] = mapped_column(nullable=False, default=list)

    allowed_models: Mapped[JsonList] = mapped_column(nullable=False, default=list)

    # Not the complement of `allowed_models`. A model in neither list is one nobody has
    # implemented; a model here is one that would be *wrong*, and only the second justifies
    # stopping a run.
    blocked_models: Mapped[JsonList] = mapped_column(nullable=False, default=list)

    required_metrics: Mapped[JsonList] = mapped_column(nullable=False, default=list)
    warnings: Mapped[JsonList] = mapped_column(nullable=False, default=list)

    created_at: Mapped[Timestamp] = created_at_column()

    __table_args__ = (
        CheckConstraint("char_length(key) > 0", name="ck_sector_profiles_key_is_present"),
        # No constraint here forbids a model appearing in both `allowed_models` and
        # `blocked_models`. Testing two JSONB arrays for *overlap* needs a subquery, which a
        # CHECK cannot contain, and the near-miss that fits — `allowed @> blocked` — asks
        # whether allowed contains *all* of blocked, which is a different question that
        # would pass on exactly the rows worth catching. A constraint that reads right and
        # tests the wrong thing is worse than none, so the invariant is asserted over
        # `aer.core.sectors.SECTOR_PROFILES` in the test suite instead, and
        # `SectorProfile.permits` makes blocked win regardless.
    )

    def __repr__(self) -> str:
        return f"<SectorProfile {self.key}>"
