"""The brief the baseline is given, and the section list it shares with the platform."""

from __future__ import annotations

from typing import Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.db.models import SectionDefinition

__all__ = ["FALLBACK_SECTION_TITLES", "section_titles"]

# The eighteen titles migration 0023 seeds, in position order, for when no database is at
# hand. The live path reads the rows so a retitled section reaches the baseline too.
FALLBACK_SECTION_TITLES: Final[tuple[str, ...]] = (
    "Executive Summary",
    "Investment Thesis",
    "Business Overview",
    "Segment Analysis",
    "Industry & Competitive Positioning",
    "Management & Governance",
    "Historical Financial Analysis",
    "Earnings Quality",
    "Balance Sheet & Liquidity",
    "Cash Flow Analysis",
    "Capital Allocation",
    "Growth Outlook",
    "Valuation - Discounted Cash Flow",
    "Scenarios & Sensitivities",
    "Key Risks",
    "Catalysts",
    "Prior Research Comparison",
    "Validation & Disagreements",
)


async def section_titles(session: AsyncSession) -> tuple[str, ...]:
    """The built-in section titles as the platform holds them, in spine order."""
    rows = list(
        await session.scalars(
            select(SectionDefinition)
            .where(SectionDefinition.origin == "builtin")
            .order_by(SectionDefinition.position)
        )
    )
    if not rows:
        return FALLBACK_SECTION_TITLES
    return tuple(str(row.title) for row in rows)
