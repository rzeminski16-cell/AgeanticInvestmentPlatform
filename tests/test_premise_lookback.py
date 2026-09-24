"""The comparison section's second half (ADR 0122, its consequences): the premises held
against the prior report and what became of them — read from the record, quoted never
re-judged, shown on the operator's copy and withheld from the copy that leaves.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.calc.comps import Audience
from aer.core.enums import (
    FindingKind,
    JudgementKind,
    PremiseComparator,
    PremiseStatus,
    PremiseVerdict,
    ProcessQuality,
    UserRole,
)
from aer.db.models import (
    Company,
    Finding,
    Judgement,
    Portfolio,
    Premise,
    Review,
    ReviewVerdict,
    SectionDefinition,
    Security,
    Thesis,
    User,
)
from aer.sections.deterministic import PRIOR_COMPARISON_KEY
from aer.sections.render import render_section
from aer.services import theses as thesis_service
from aer.services.history import comparison_for_audience, prior_comparison_content
from aer.services.premise_outcomes import lookback_rows, premise_outcomes_for
from aer.services.theses import Predicate
from tests.test_assumption_outcomes import READING_AS_OF, _company, _prior_report
from tests.workflow_fixtures import seed_job, seed_request, seed_user

pytestmark = pytest.mark.integration

WRITTEN = datetime(2022, 7, 15, 9, tzinfo=UTC)
REVIEWED = datetime(2023, 9, 1, 9, tzinfo=UTC)


async def _review(
    session: AsyncSession,
    *,
    user: User,
    company: Company,
    thesis: Thesis,
    premise: Premise,
    verdict: PremiseVerdict,
    note: str = "",
) -> Review:
    """A confirmed post-trade review of a closed position on the company, with one verdict
    on the premise. Seeded as rows: the reviewer's own pass is held elsewhere."""
    book = Portfolio(user_id=user.id, name=f"Book of {user.display_name}", base_currency="USD")
    session.add(book)
    security = Security(
        company_id=company.id,
        ticker=company.ticker,
        exchange="NASDAQ",
        provider_symbol=f"{company.ticker}.US",
        name=company.name,
        quote_currency="USD",
    )
    session.add(security)
    await session.flush()
    review = Review(
        judgement=Judgement(
            kind=JudgementKind.REVIEW,
            held_by=user.email,
            held_at=REVIEWED,
            basis="The position closed and I read the record against it.",
        ),
        portfolio_id=book.id,
        security_id=security.id,
        opened_on=date(2022, 8, 1),
        closed_on=date(2023, 8, 1),
        thesis_id=thesis.id,
        process_quality=ProcessQuality.SOUND,
        outcome={"realised_return": "0.1", "holding_days": 365},
    )
    session.add(review)
    await session.flush()
    session.add(
        ReviewVerdict(
            review_id=review.judgement_id,
            premise_id=premise.judgement_id,
            position=1,
            statement=premise.statement,
            verdict=verdict,
            note=note,
        )
    )
    await session.flush()
    return review


async def _scene(session: AsyncSession) -> dict[str, Any]:
    """A prior report; a thesis written against it with a premise the monitor read and
    contradicted and a review found failed, and a premise a person reviews, since
    withdrawn; a thesis not written against the report; another person's thesis that was."""
    user = await seed_user(session, email="lookback@example.invalid")
    company = await _company(session, ticker="MSFT")
    prior = await _prior_report(session, user=user, company=company, assumptions={})

    thesis = await thesis_service.write_thesis(
        session,
        user=user,
        company=company,
        title="Margins hold",
        written_at=WRITTEN,
        report_id=prior.id,
    )
    tested = await thesis_service.add_premise(
        session,
        thesis=thesis,
        actor=user,
        statement="Operating margin stays above 20%.",
        basis="Ten years of history.",
        predicate=Predicate(
            metric="operating margin",
            comparator=PremiseComparator.AT_LEAST,
            threshold=Decimal("0.20"),
            unit="ratio",
        ),
        review_by=None,
        held_at=WRITTEN,
    )
    reviewed = await thesis_service.add_premise(
        session,
        thesis=thesis,
        actor=user,
        statement="Management allocates capital well.",
        basis="Buybacks below intrinsic value.",
        predicate=None,
        review_by=date(2024, 3, 31),
        held_at=WRITTEN,
    )
    session.add(
        Finding(
            user_id=user.id,
            thesis_id=thesis.id,
            judgement_id=tested.judgement_id,
            kind=FindingKind.READING,
            status=PremiseStatus.CONTRADICTED,
            justification="FY23 operating margin came in at 18%.",
            opens_gate=True,
        )
    )
    await session.flush()
    await thesis_service.withdraw_premise(
        session,
        premise=reviewed,
        actor=user,
        reason="Capital allocation changed with the new chief executive.",
    )
    review = await _review(
        session,
        user=user,
        company=company,
        thesis=thesis,
        premise=tested,
        verdict=PremiseVerdict.FAILED,
        note="The margin fell under 20% in the first year.",
    )

    unanchored = await thesis_service.write_thesis(
        session, user=user, company=company, title="Unanchored"
    )
    await thesis_service.add_premise(
        session,
        thesis=unanchored,
        actor=user,
        statement="Written against nothing in particular.",
        basis="A hunch.",
        predicate=None,
        review_by=date(2027, 1, 1),
    )
    other = User(email="other@example.invalid", display_name="Other", role=UserRole.ANALYST)
    session.add(other)
    await session.flush()
    theirs = await thesis_service.write_thesis(
        session, user=other, company=company, title="Theirs", report_id=prior.id
    )
    await thesis_service.add_premise(
        session,
        thesis=theirs,
        actor=other,
        statement="Their premise holds.",
        basis="Their reasons.",
        predicate=None,
        review_by=date(2027, 1, 1),
    )
    return {
        "session": session,
        "user": user,
        "company": company,
        "prior": prior,
        "thesis": thesis,
        "tested": tested,
        "reviewed": reviewed,
        "review": review,
    }


@pytest.fixture
async def scene(db_session: AsyncSession) -> dict[str, Any]:
    return await _scene(db_session)


class TestTheOutcomes:
    async def test_they_are_this_persons_premises_against_the_report(
        self, scene: dict[str, Any]
    ) -> None:
        outcomes = await premise_outcomes_for(
            scene["session"], report=scene["prior"], user_id=scene["user"].id
        )

        assert [outcome.premise.statement for outcome in outcomes] == [
            "Operating margin stays above 20%.",
            "Management allocates capital well.",
        ]
        assert all(outcome.thesis is not None for outcome in outcomes)

    async def test_what_became_of_them_is_quoted_from_the_rows(self, scene: dict[str, Any]) -> None:
        tested, reviewed = await premise_outcomes_for(
            scene["session"], report=scene["prior"], user_id=scene["user"].id
        )

        assert tested.held == "Held from 15 July 2022: operating margin at least 0.2 ratio."
        assert "Last read by the monitor on" in tested.state
        assert "as contradicted" in tested.state
        assert tested.state.endswith(
            "A post-trade review found it failed — The margin fell under 20% in the first year."
        )
        assert reviewed.held == (
            "Held from 15 July 2022; no predicate, to be looked at again by 31 March 2024."
        )
        assert reviewed.state.startswith("Withdrawn on ")
        assert reviewed.state.endswith("Capital allocation changed with the new chief executive.")

    async def test_a_retired_thesis_says_so_first(self, scene: dict[str, Any]) -> None:
        await thesis_service.retire_thesis(
            scene["session"],
            thesis=scene["thesis"],
            actor=scene["user"],
            reason="Sold out on the margin miss.",
        )

        tested, _ = await premise_outcomes_for(
            scene["session"], report=scene["prior"], user_id=scene["user"].id
        )

        assert tested.state.startswith("The thesis was retired on ")
        assert "Sold out on the margin miss" in tested.state

    async def test_the_rows_take_the_comparison_sections_shape(self, scene: dict[str, Any]) -> None:
        outcomes = await premise_outcomes_for(
            scene["session"], report=scene["prior"], user_id=scene["user"].id
        )

        rows = lookback_rows(outcomes)

        assert [row["aspect"] for row in rows] == [
            "Premise — Operating margin stays above 20%.",
            "Premise — Management allocates capital well.",
        ]
        assert {row["prior_report_id"] for row in rows} == {str(scene["prior"].id)}
        assert set(rows[0]) == {"aspect", "prior", "current", "prior_report_id"}


class TestTheSection:
    async def _content(self, scene: dict[str, Any]) -> dict[str, Any]:
        reading = await seed_request(scene["session"], user=scene["user"], as_of_date=READING_AS_OF)
        job = await seed_job(scene["session"], request=reading)
        return await prior_comparison_content(scene["session"], job_id=job.id, request=reading)

    async def test_the_look_back_is_carried_apart_from_the_research(
        self, scene: dict[str, Any]
    ) -> None:
        content = await self._content(scene)

        assert [row["aspect"] for row in content["premises"]] == [
            "Premise — Operating margin stays above 20%.",
            "Premise — Management allocates capital well.",
        ]
        assert not any(row["aspect"].startswith("Premise") for row in content["comparisons"])

    async def test_the_operators_copy_shows_it_and_the_copy_that_leaves_says_so(
        self, scene: dict[str, Any]
    ) -> None:
        """Rendered against the seeded contract, as the document renders it: the rows join
        the one table on the operator's copy; the shareable copy carries the sentence and
        not one word of a premise."""
        content = await self._content(scene)
        definition = await scene["session"].scalar(
            select(SectionDefinition).where(SectionDefinition.key == PRIOR_COMPARISON_KEY)
        )
        assert definition is not None

        def rendered(audience: Audience) -> str:
            return render_section(
                key=PRIOR_COMPARISON_KEY,
                title=definition.title,
                contract=definition.output_contract,
                content=comparison_for_audience(content, audience),
            ).markdown

        own = rendered(Audience.INTERNAL)
        shared = rendered(Audience.SHAREABLE)

        assert "Premise — Operating margin stays above 20%." in own
        assert "A post-trade review found it failed" in own
        assert "Withdrawn on" in own
        assert "Premise —" not in shared
        assert "Operating margin stays above" not in shared
        assert "A post-trade review" not in shared
        assert (
            "2 premises held against the prior research, and what became of them, are "
            "withheld from this copy"
        ) in shared
