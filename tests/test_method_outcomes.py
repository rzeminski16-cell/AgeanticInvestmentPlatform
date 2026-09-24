"""Which method produced which outcome (ADR 0122 §2, the methodology library).

A skill's record is read over the approved reports a person's runs pinned it in: how far
the confirmed drivers landed from what was later filed, and what became of the theses
written against those reports — decisions, the reviews' process grades, the verdicts on
the premises. A skill set aside, a skill on a run that never reached an approved report and
another person's runs are not in it. The library's row says it in one sentence.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aer.core.enums import DecisionAction, PremiseComparator, PremiseVerdict, UserRole
from aer.db.models import PlanSkillPin, Skill, SkillVersion, User
from aer.db.models.plan_skill_pin import PLANNED, SKIPPED_NOT_APPLICABLE
from aer.services import decisions as decision_service
from aer.services import theses as thesis_service
from aer.services.method_outcomes import method_records
from aer.services.skills import save_skill
from aer.services.theses import Predicate
from tests.api_fixtures import build_app, client_for
from tests.db_cleanup import delete_all
from tests.test_assumption_outcomes import (
    PRIOR_AS_OF,
    _company,
    _document,
    _fiscal_year,
    _prior_report,
)
from tests.test_premise_lookback import _review
from tests.test_skills_surface import SKILL_SOURCE
from tests.workflow_fixtures import seed_request, seed_user

pytestmark = pytest.mark.integration

WRITTEN = datetime(2022, 7, 15, 9, tzinfo=UTC)


async def _pin(
    session: AsyncSession,
    *,
    source: str,
    user: User,
    work_order_id: Any,
    status: str = PLANNED,
    reason: str = "",
) -> Skill:
    version = await save_skill(session, source=source, actor=user)
    skill = await session.get(Skill, version.skill_id)
    assert skill is not None
    session.add(
        PlanSkillPin(
            work_order_id=work_order_id,
            skill_id=skill.id,
            skill_version_id=version.id,
            status=status,
            reason=reason,
            estimated_cost_gbp=Decimal("0.12"),
        )
    )
    await session.flush()
    return skill


async def _scene(session: AsyncSession) -> dict[str, Any]:
    """A prior run that pinned the skill and assumed 9% growth against a year that filed
    6.4%; a thesis written against its report, a buy decision on it, and a review that
    found the premise held."""
    user = await seed_user(session, email="methods@example.invalid")
    company = await _company(session, ticker="MSFT")
    prior = await _prior_report(
        session,
        user=user,
        company=company,
        assumptions={"revenue_growth": Decimal("0.090000")},
    )
    document = await _document(session, request_id=prior.request_id, job_id=prior.job_id)
    await _fiscal_year(
        session,
        company=company,
        document=document,
        period_end=PRIOR_AS_OF,
        filed=date(2022, 7, 28),
        lines={"revenue": Decimal("100000000")},
    )
    await _fiscal_year(
        session,
        company=company,
        document=document,
        period_end=date(2023, 6, 30),
        filed=date(2023, 7, 27),
        lines={"revenue": Decimal("106400000")},
    )
    skill = await _pin(session, source=SKILL_SOURCE, user=user, work_order_id=prior.request_id)

    thesis = await thesis_service.write_thesis(
        session,
        user=user,
        company=company,
        title="Margins hold",
        written_at=WRITTEN,
        report_id=prior.id,
    )
    premise = await thesis_service.add_premise(
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
    loaded = await thesis_service.thesis_of(session, thesis.id, user_id=user.id)
    assert loaded is not None
    await decision_service.record_decision(
        session,
        actor=user,
        thesis=loaded,
        action=DecisionAction.BUY,
        statement="Open an initial position.",
        basis="The report confirmed the margin structure.",
        decided_at=WRITTEN,
    )
    await _review(
        session,
        user=user,
        company=company,
        thesis=thesis,
        premise=premise,
        verdict=PremiseVerdict.HELD,
    )
    return {
        "session": session,
        "user": user,
        "company": company,
        "prior": prior,
        "skill": skill,
        "thesis": thesis,
    }


@pytest.fixture
async def scene(db_session: AsyncSession) -> dict[str, Any]:
    return await _scene(db_session)


class TestTheRecord:
    async def test_a_method_is_judged_by_what_it_shaped(self, scene: dict[str, Any]) -> None:
        records = await method_records(scene["session"], user_id=scene["user"].id)

        record = records["moat_durability"]
        assert record.reports == 1
        assert record.drivers_measured == 1
        assert record.mean_absolute_delta == "0.026"
        assert record.theses == 1
        assert record.decisions == 1
        assert record.reviews == 1
        assert record.process == (("sound", 1),)
        assert record.verdicts == (("held", 1),)
        assert record.sentence == (
            "Ran in 1 approved report; 1 confirmed driver measured against the year forecast, "
            "mean absolute delta 0.026; 1 thesis written against those reports, 1 decision; "
            "1 review (sound 1); premises held 1."
        )

    async def test_a_skill_set_aside_or_never_approved_has_no_record(
        self, scene: dict[str, Any]
    ) -> None:
        session = scene["session"]
        await _pin(
            session,
            source=SKILL_SOURCE.replace("moat_durability", "moat_breadth"),
            user=scene["user"],
            work_order_id=scene["prior"].request_id,
            status=SKIPPED_NOT_APPLICABLE,
            reason="Not a question this run asks.",
        )
        unfinished = await seed_request(session, user=scene["user"], as_of_date=date(2025, 6, 30))
        await _pin(
            session,
            source=SKILL_SOURCE.replace("moat_durability", "moat_depth"),
            user=scene["user"],
            work_order_id=unfinished.id,
        )

        records = await method_records(session, user_id=scene["user"].id)

        assert set(records) == {"moat_durability"}

    async def test_another_persons_runs_are_not_this_persons_record(
        self, scene: dict[str, Any]
    ) -> None:
        session = scene["session"]
        other = User(email="other@example.invalid", display_name="Other", role=UserRole.ANALYST)
        session.add(other)
        await session.flush()
        theirs = await _prior_report(session, user=other, company=scene["company"], assumptions={})
        version = await session.scalar(
            select(SkillVersion).where(SkillVersion.skill_id == scene["skill"].id)
        )
        assert version is not None
        session.add(
            PlanSkillPin(
                work_order_id=theirs.request_id,
                skill_id=scene["skill"].id,
                skill_version_id=version.id,
                status=PLANNED,
                reason="",
                estimated_cost_gbp=Decimal("0.12"),
            )
        )
        await session.flush()

        mine = await method_records(session, user_id=scene["user"].id)
        not_mine = await method_records(session, user_id=other.id)

        assert mine["moat_durability"].reports == 1
        assert mine["moat_durability"].theses == 1
        assert not_mine["moat_durability"].reports == 1
        assert not_mine["moat_durability"].theses == 0
        assert not_mine["moat_durability"].sentence.endswith(
            "no thesis written against those reports yet."
        )


class TestThePage:
    @pytest.fixture
    async def committed(self, db_engine: Any) -> AsyncIterator[dict[str, Any]]:
        await delete_all(db_engine)
        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as session:
            scene = await _scene(session)
            await session.commit()
            yield scene

    @pytest.fixture
    async def api(
        self, api_settings: Any, db_engine: Any, fake_redis: Any, committed: Any
    ) -> AsyncIterator[Any]:
        app = build_app(api_settings, engine=db_engine, redis=fake_redis)
        async for client in client_for(app):
            yield client

    async def test_the_library_row_carries_the_record(self, api: Any, committed: Any) -> None:
        body = (await api.get("/skills")).text

        assert 'data-record="moat_durability"' in body
        assert "Ran in 1 approved report; 1 confirmed driver measured" in body
        assert "premises held 1." in body
