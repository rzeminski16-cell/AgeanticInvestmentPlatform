"""Who a report is about, by the name the filings use.

Gap A67. A live note's Prior Research Comparison opened *"This is the first research run
for M&T Banking Corporation (MTB)"* — the operator's typo for M&T **Bank** Corporation,
preserved verbatim, in a report whose front matter said "M&T BANK CORP" three lines above.

**``ResearchRequest.company_name`` is an input field, not an identity.** It is what somebody
typed into a form to start a search, it is never checked against anything, and it is the
easiest string in the codebase to reach. The identity is on the company row, written from
the filer's own submission the moment ``sec.entity_resolved`` fires, and every surface a
reader meets should use that one.

The fallback to the typed name is not a compromise. Before the entity is resolved — the
plan gate, a request that has not run — the typed name is the *only* answer, and it is the
honest one: it is what the operator asked for, and nothing yet says otherwise.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.db.models import Company, ResearchRequest

__all__ = ["name_of", "subject_name"]


def name_of(request: ResearchRequest, company: Company | None) -> str:
    """What to call the subject, given a company row a caller already holds.

    The rule in one place, so a surface that reaches for a name gets the same answer
    wherever it sits. A company row whose name is blank falls back too: an empty heading
    is worse than an unverified one.
    """
    if company is not None and company.name.strip():
        return company.name
    return request.company_name


async def subject_name(session: AsyncSession, request: ResearchRequest) -> str:
    """What to call the subject of this request, resolved from the run's own rows.

    Prefers ``request.company_id`` — written by ``acquire`` and the one authoritative
    answer to "whose report is this" — and falls back to the ticker and exchange for a
    caller running before that link is made. Returns the typed name when neither finds a
    company, which is every request that has not yet resolved its entity.
    """
    company: Company | None = None
    if request.company_id is not None:
        company = await session.get(Company, request.company_id)
    if company is None:
        company = await session.scalar(
            select(Company).where(
                Company.ticker == request.ticker, Company.exchange == request.exchange
            )
        )
    return name_of(request, company)
