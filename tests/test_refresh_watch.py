"""The refresh reads the premises the operator holds on the company (ADR 0122 §2).

The figures a held premise names are material at half the ordinary threshold; a crossing is
judged as before; and a retired thesis, a withdrawn premise, a premise a person reviews, a
metric nothing resolves and another person's thesis name nothing. Held on the service's own
watch and on the pure diff it feeds, without a run.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from aer.calc.changes import Figure, Movement, diff_figures
from aer.core.enums import PremiseComparator, UserRole
from aer.db.models import Company, Thesis, User
from aer.services import refresh as refresh_service
from aer.services import theses as thesis_service
from aer.services.theses import Predicate
from aer.services.thesis_monitor import resolve_metric
from tests.request_fixtures import research_request
from tests.test_knowledge_stats import _company, _user

pytestmark = pytest.mark.integration


async def _scene(session: AsyncSession) -> dict[str, Any]:
    user = await _user(session)
    company = await _company(session, "WTCH", "Watch plc")
    request = research_request(
        user_id=user.id,
        company_id=company.id,
        company_name=company.name,
        ticker="WTCH",
        exchange="NASDAQ",
        as_of_date=date(2026, 6, 30),
        base_currency="USD",
        reporting_currency="USD",
        investment_horizon_months=12,
        max_cost_gbp="2.50",
    )
    session.add(request)
    await session.flush()
    return {"session": session, "user": user, "company": company, "request": request}


async def _held(
    scene: dict[str, Any],
    *,
    metric: str | None,
    title: str,
    user: User | None = None,
    company: Company | None = None,
) -> tuple[Thesis, Any]:
    session: AsyncSession = scene["session"]
    holder = user or scene["user"]
    thesis = await thesis_service.write_thesis(
        session, user=holder, company=company or scene["company"], title=title
    )
    premise = await thesis_service.add_premise(
        session,
        thesis=thesis,
        actor=holder,
        statement=f"{metric or 'Management'} holds up.",
        basis="History.",
        predicate=(
            Predicate(
                metric=metric,
                comparator=PremiseComparator.AT_LEAST,
                threshold=Decimal("0.2"),
                unit="ratio",
            )
            if metric is not None
            else None
        ),
        review_by=None if metric is not None else date(2027, 3, 31),
    )
    return thesis, premise


def _key(metric: str) -> tuple[str, str]:
    resolved = resolve_metric(metric)
    assert resolved is not None
    return ("fact" if resolved.kind == "level" else "calculation", resolved.key)


class TestTheWatch:
    async def test_the_figures_a_held_premise_reads_are_watched(
        self, db_session: AsyncSession
    ) -> None:
        scene = await _scene(db_session)
        await _held(scene, metric="operating margin", title="Margins")

        watch = await refresh_service.premise_watch(db_session, scene["request"])

        assert watch.keys == frozenset({_key("operating margin")})
        assert watch.crossed is not None

    async def test_a_level_is_a_fact_and_a_ratio_a_calculation(
        self, db_session: AsyncSession
    ) -> None:
        scene = await _scene(db_session)
        thesis, _ = await _held(scene, metric="revenue", title="Scale")
        await thesis_service.add_premise(
            db_session,
            thesis=thesis,
            actor=scene["user"],
            statement="Margins hold up.",
            basis="History.",
            predicate=Predicate(
                metric="operating margin",
                comparator=PremiseComparator.AT_LEAST,
                threshold=Decimal("0.2"),
                unit="ratio",
            ),
            review_by=None,
        )

        watch = await refresh_service.premise_watch(db_session, scene["request"])

        assert watch.keys == frozenset({_key("revenue"), _key("operating margin")})
        assert _key("revenue")[0] == "fact"
        assert _key("operating margin")[0] == "calculation"

    async def test_what_is_not_held_names_nothing(self, db_session: AsyncSession) -> None:
        """A retired thesis, a withdrawn premise, a premise a person reviews and a metric
        nothing resolves: none of them is a figure the operator's position rests on."""
        scene = await _scene(db_session)
        retired, _ = await _held(scene, metric="operating margin", title="Retired")
        await thesis_service.retire_thesis(
            db_session, thesis=retired, actor=scene["user"], reason="Sold out of it."
        )
        _, withdrawn = await _held(scene, metric="net margin", title="Withdrawn")
        await thesis_service.withdraw_premise(
            db_session, premise=withdrawn, actor=scene["user"], reason="No longer believed."
        )
        await _held(scene, metric=None, title="Reviewed by a person")
        await _held(scene, metric="brand warmth", title="Unresolvable")

        watch = await refresh_service.premise_watch(db_session, scene["request"])

        assert watch.keys == frozenset()
        assert watch.crossed is None

    async def test_another_persons_premise_is_not_this_persons_watch(
        self, db_session: AsyncSession
    ) -> None:
        scene = await _scene(db_session)
        other = User(email="other@example.invalid", display_name="Other", role=UserRole.ANALYST)
        db_session.add(other)
        await db_session.flush()
        await _held(scene, metric="operating margin", title="Theirs", user=other)

        watch = await refresh_service.premise_watch(db_session, scene["request"])

        assert watch.keys == frozenset()

    async def test_a_request_on_no_company_watches_nothing(self, db_session: AsyncSession) -> None:
        scene = await _scene(db_session)
        await _held(scene, metric="operating margin", title="Margins")
        scene["request"].company_id = None
        await db_session.flush()

        watch = await refresh_service.premise_watch(db_session, scene["request"])

        assert watch.keys == frozenset()
        assert watch.crossed is None


class TestTheDiffItFeeds:
    async def test_a_one_per_cent_move_in_a_watched_figure_is_material(
        self, db_session: AsyncSession
    ) -> None:
        scene = await _scene(db_session)
        await _held(scene, metric="operating margin", title="Margins")
        watch = await refresh_service.premise_watch(db_session, scene["request"])
        kind, name = _key("operating margin")

        def figure(figure_name: str, value: str) -> Figure:
            return Figure(
                kind=kind, name=figure_name, value=Decimal(value), unit="pure", period="FY 2026"
            )

        [watched, unwatched] = diff_figures(
            [figure(name, "0.2500"), figure("asset_turnover", "0.2500")],
            [figure(name, "0.2525"), figure("asset_turnover", "0.2525")],
            crossed=watch.crossed,
            watched=watch.keys,
        )

        assert watched.material
        assert watched.movement is Movement.WATCHED
        assert watched.narrative.endswith(
            "It feeds a premise you hold, and is material at half the ordinary threshold."
        )
        assert not unwatched.material
        assert unwatched.movement is Movement.UNCHANGED

    async def test_a_crossing_still_outranks_the_watch(self, db_session: AsyncSession) -> None:
        scene = await _scene(db_session)
        await _held(scene, metric="operating margin", title="Margins")
        watch = await refresh_service.premise_watch(db_session, scene["request"])
        kind, name = _key("operating margin")

        [change] = diff_figures(
            [Figure(kind=kind, name=name, value=Decimal("0.21"), unit="pure", period="FY 2026")],
            [Figure(kind=kind, name=name, value=Decimal("0.19"), unit="pure", period="FY 2026")],
            crossed=watch.crossed,
            watched=watch.keys,
        )

        assert change.movement is Movement.PREMISE
        assert "It crosses the premise" in change.narrative
