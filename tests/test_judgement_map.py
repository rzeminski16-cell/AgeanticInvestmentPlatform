"""The map learns what you decided (ADR 0122 §1, Phase 6b.1).

Four kinds of node — thesis, premise, decision, verdict — under the rule every other node
is held to: only confirmed state produces one. The vault writes a thesis note and the
company note links it; the statistics count the layer; the drawing gains the four kinds
with a legend and a filter. A decision is pinned to the premises as they stood when it was
held, reconstructed from the judgements' own clocks, because no version is stored.
"""

from __future__ import annotations

import re
import uuid
from collections import Counter
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import frontmatter
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aer.config import Settings
from aer.core.enums import (
    DecisionAction,
    FindingKind,
    JudgementKind,
    PremiseComparator,
    PremiseStatus,
)
from aer.db.models import Finding, Judgement, Premise
from aer.obsidian.export import export_report
from aer.obsidian.judgements import judgement_views, thesis_subjects, version_at
from aer.services import decisions as decision_service
from aer.services import theses as thesis_service
from aer.services.graph_view import KINDS, graph_picture
from aer.services.knowledge import knowledge_stats
from aer.services.theses import Predicate
from tests.api_fixtures import build_app, client_for
from tests.test_knowledge_stats import _company, _run, _user

pytestmark = pytest.mark.integration

WRITTEN = datetime(2026, 3, 1, 9, tzinfo=UTC)
_WIKI_LINK = re.compile(r"\[\[([^\]]+)\]\]")


async def _scene(session: AsyncSession) -> dict[str, Any]:
    """Alpha, researched, with a thesis of two premises (one read and contradicted), a buy
    decision held between the two premises' dates; Beta, believed about and never
    researched."""
    user = await _user(session)
    alpha = await _company(session, "ALPH", "Alpha plc")
    beta = await _company(session, "BETA", "Beta plc")
    report = await _run(session, user=user, company=alpha)

    thesis = await thesis_service.write_thesis(
        session,
        user=user,
        company=alpha,
        title="Alpha compounds at twelve percent",
        written_at=WRITTEN,
        report_id=report.id,
    )
    first = await thesis_service.add_premise(
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
    second = await thesis_service.add_premise(
        session,
        thesis=thesis,
        actor=user,
        statement="Management allocates capital well.",
        basis="Buybacks below intrinsic value.",
        predicate=None,
        review_by=date(2027, 3, 31),
        held_at=WRITTEN + timedelta(days=30),
    )
    loaded = await thesis_service.thesis_of(session, thesis.id, user_id=user.id)
    assert loaded is not None
    decision = await decision_service.record_decision(
        session,
        actor=user,
        thesis=loaded,
        action=DecisionAction.BUY,
        statement="Open an initial position.",
        basis="The report confirmed the margin structure.",
        size_statement="about 2% of the book",
        horizon_months=24,
        decided_at=WRITTEN + timedelta(days=10),
    )
    reading = Finding(
        user_id=user.id,
        thesis_id=thesis.id,
        judgement_id=first.judgement_id,
        kind=FindingKind.READING,
        status=PremiseStatus.CONTRADICTED,
        justification="FY26 operating margin came in at 18%.",
        # A contradicted reading opens a gate (ADR 0079): the tier as data, and the check
        # constraint on the table refuses a contradicted reading that says otherwise.
        opens_gate=True,
    )
    session.add(reading)
    await session.flush()
    other = await thesis_service.write_thesis(
        session, user=user, company=beta, title="Beta is cheap for a reason"
    )
    return {
        "session": session,
        "user": user,
        "alpha": alpha,
        "beta": beta,
        "report": report,
        "thesis": thesis,
        "first": first,
        "second": second,
        "decision": decision,
        "other": other,
    }


def _premise(position: int, held_at: datetime, withdrawn_at: datetime | None = None) -> Premise:
    return Premise(
        position=position,
        statement=f"Premise {position}",
        judgement=Judgement(
            kind=JudgementKind.PREMISE,
            held_by="me@example.invalid",
            held_at=held_at,
            basis="Because.",
            withdrawn_at=withdrawn_at,
            withdrawn_reason="No longer held." if withdrawn_at else None,
        ),
    )


class TestTheVersion:
    def test_the_version_is_the_premises_as_they_stood(self) -> None:
        """Written by then and not yet withdrawn, in thesis order — from the clocks alone."""
        early = _premise(1, WRITTEN)
        late = _premise(2, WRITTEN + timedelta(days=30))
        gone = _premise(3, WRITTEN, withdrawn_at=WRITTEN + timedelta(days=5))

        assert version_at([late, gone, early], WRITTEN + timedelta(days=10)) == (early,)
        assert version_at([late, gone, early], WRITTEN + timedelta(days=2)) == (early, gone)
        assert version_at([late, gone, early], WRITTEN + timedelta(days=40)) == (early, late)
        assert version_at([late, gone, early], WRITTEN - timedelta(days=1)) == ()


class TestTheViews:
    async def test_a_thesis_view_carries_its_layer(self, db_session: AsyncSession) -> None:
        scene = await _scene(db_session)

        views = await judgement_views(
            db_session,
            companies={scene["alpha"].id: scene["alpha"], scene["beta"].id: scene["beta"]},
            user_id=scene["user"].id,
        )

        # Alpha's was formed in March by the operator's own clock; Beta's only when written.
        [alpha_view, beta_view] = views
        assert alpha_view.thesis.id == scene["thesis"].id
        assert beta_view.thesis.id == scene["other"].id
        assert [row.premise.statement for row in alpha_view.premises] == [
            "Operating margin stays above 20%.",
            "Management allocates capital well.",
        ]
        first, second = alpha_view.premises
        assert first.reading is not None
        assert first.reading.status is PremiseStatus.CONTRADICTED
        assert second.reading is None
        [decision_view] = alpha_view.decisions
        assert decision_view.decision.judgement_id == scene["decision"].judgement_id
        # Held ten days after the first premise and twenty before the second.
        assert [row.statement for row in decision_view.version] == [
            "Operating margin stays above 20%."
        ]
        assert decision_view.verdict is None
        assert alpha_view.verdicts == ()
        assert beta_view.premises == ()
        assert beta_view.decisions == ()

    async def test_another_persons_theses_are_not_this_persons(
        self, db_session: AsyncSession
    ) -> None:
        scene = await _scene(db_session)

        views = await judgement_views(
            db_session, companies={scene["alpha"].id: scene["alpha"]}, user_id=uuid.uuid4()
        )

        assert views == ()

    async def test_a_company_believed_about_is_a_subject(self, db_session: AsyncSession) -> None:
        scene = await _scene(db_session)

        subjects = await thesis_subjects(db_session)

        assert set(subjects) == {scene["alpha"].id, scene["beta"].id}


class TestTheStatistics:
    async def test_the_size_counts_the_judgement_layer(self, db_session: AsyncSession) -> None:
        await _scene(db_session)

        stats = await knowledge_stats(db_session, as_of=date(2026, 9, 24))

        assert stats.size.thesis_nodes == 2
        assert stats.size.premise_nodes == 2
        assert stats.size.decision_nodes == 1
        assert stats.size.verdict_nodes == 0
        # Beta is in the map because somebody holds a thesis on it: a stub, never researched.
        assert stats.size.companies == 2
        assert stats.size.researched == 1
        assert stats.size.stubs == 1
        assert stats.size.as_dict()["thesis_nodes"] == 2


class TestTheDrawing:
    async def test_the_drawing_gains_the_four_kinds(self, db_session: AsyncSession) -> None:
        await _scene(db_session)

        picture = await graph_picture(db_session)

        assert picture.shown == KINDS
        assert picture.counts == {
            "company": 2,
            "theme": 0,
            "thesis": 2,
            "premise": 2,
            "decision": 1,
            "verdict": 0,
        }
        nodes = {placed.node.title: placed.node for placed in picture.nodes}
        assert nodes["Operating margin stays above 20%."].state == "contradicted"
        assert nodes["Management allocates capital well."].state == "unread"
        assert nodes["Alpha compounds at twelve percent"].href.startswith("/theses/")
        assert nodes["BETA — Beta plc"].researched is False
        assert any(
            node.kind == "decision" and node.label == "open a position" for node in nodes.values()
        )
        edges = Counter(edge.kind for edge in picture.edges)
        assert edges["holds"] == 2
        assert edges["asserts"] == 2
        assert edges["acts_on"] == 1
        assert "scores" not in edges

    async def test_the_filter_narrows_the_drawing_and_not_the_counts(
        self, db_session: AsyncSession
    ) -> None:
        await _scene(db_session)

        picture = await graph_picture(db_session, kinds=["company", "thesis"])

        assert picture.shown == ("company", "thesis")
        assert {placed.node.kind for placed in picture.nodes} == {"company", "thesis"}
        assert {edge.kind for edge in picture.edges} == {"holds"}
        assert picture.counts["premise"] == 2
        nothing = await graph_picture(db_session, kinds=["verdict"])
        assert nothing.nodes == ()
        everything = await graph_picture(db_session, kinds=["not-a-kind"])
        assert everything.shown == KINDS


class TestTheVault:
    async def test_the_vault_writes_the_thesis_and_the_company_links_it(
        self, db_session: AsyncSession, tmp_path: Any
    ) -> None:
        scene = await _scene(db_session)
        settings = Settings(
            http_user_agent="Tracework Test test@example.invalid",
            artefact_root=tmp_path / "artefacts",
            obsidian_vault_root=tmp_path / "vault",
            obsidian_personal_root=tmp_path / "personal",
        )

        await export_report(db_session, settings=settings, report_id=scene["report"].id)

        vault = tmp_path / "vault"
        [note_path] = list(
            (vault / "60-Theses").glob("ALPH - Thesis - Alpha compounds at twelve percent - *.md")
        )
        note = frontmatter.loads(note_path.read_text(encoding="utf-8"))
        assert note["aer_kind"] == "thesis"
        assert note["premises_held"] == 2
        assert note["premises_withdrawn"] == 0
        assert note["decisions"] == 1
        assert note["verdicts"] == 0
        assert note["retired"] is False
        assert note["company_note"] == "[[ALPH - Alpha plc]]"
        assert str(note["evidence_policy"]).startswith("operator's own judgement")
        body = note.content
        assert "1. Operating margin stays above 20%." in body
        assert "last read" in body
        assert "contradicted" in body
        assert "2. Management allocates capital well. — a person reviews it by 2027-03-31" in body
        assert (
            "open a position — Open an initial position. · about 2% of the book · over 24 "
            "months — on premises 1 as they stood"
        ) in body
        assert "- Against: [[" in body
        assert "cites nothing" in body

        company = frontmatter.loads(
            (vault / "10-Companies" / "ALPH - Alpha plc.md").read_text(encoding="utf-8")
        )
        assert company["theses"] == [f"[[{note_path.stem}]]"]
        assert "## Theses" in company.content

        # Beta is outside this report's component, so its thesis is not in this export.
        assert not list((vault / "60-Theses").glob("BETA*"))
        # Closure: every link the export wrote names a file the export wrote.
        stems = {path.stem for path in vault.rglob("*.md")}
        for path in vault.rglob("*.md"):
            for target in _WIKI_LINK.findall(path.read_text(encoding="utf-8")):
                name = target.split("|")[0].split("#")[0].strip()
                assert name in stems, f"{path.name} links [[{name}]], which no file resolves"

    async def test_a_withdrawn_premise_and_a_retired_thesis_stay_on_the_record(
        self, db_session: AsyncSession, tmp_path: Any
    ) -> None:
        scene = await _scene(db_session)
        await thesis_service.withdraw_premise(
            db_session,
            premise=scene["second"],
            actor=scene["user"],
            reason="The capital allocation changed with the new chair.",
        )
        loaded = await thesis_service.thesis_of(
            db_session, scene["thesis"].id, user_id=scene["user"].id
        )
        assert loaded is not None
        await thesis_service.retire_thesis(
            db_session, thesis=loaded, actor=scene["user"], reason="Sold out in full."
        )
        settings = Settings(
            http_user_agent="Tracework Test test@example.invalid",
            artefact_root=tmp_path / "artefacts",
            obsidian_vault_root=tmp_path / "vault",
            obsidian_personal_root=tmp_path / "personal",
        )

        await export_report(db_session, settings=settings, report_id=scene["report"].id)

        [note_path] = list((tmp_path / "vault" / "60-Theses").glob("ALPH - Thesis - *.md"))
        note = frontmatter.loads(note_path.read_text(encoding="utf-8"))
        assert note["retired"] is True
        assert note["premises_held"] == 1
        assert note["premises_withdrawn"] == 1
        assert "aer/retired" in note["tags"]
        assert "withdrawn" in note.content
        assert "The capital allocation changed with the new chair." in note.content
        assert "Retired" in note.content
        assert "Sold out in full." in note.content


class TestThePages:
    @pytest.fixture
    async def committed(self, db_engine: Any) -> AsyncIterator[dict[str, Any]]:
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

    async def test_the_graph_page_has_a_legend_and_a_filter(self, api: Any) -> None:
        body = (await api.get("/knowledge/graph")).text
        assert 'data-legend="thesis"' in body
        assert 'data-count="premise">2<' in body
        assert 'class="node-premise"' in body
        assert 'id="graph-filter"' in body
        assert "asserts" in body

        narrowed = (await api.get("/knowledge/graph?kind=company&kind=thesis")).text
        assert 'class="node-thesis"' in narrowed
        assert 'class="node-premise"' not in narrowed
        assert 'data-count="premise">2<' in narrowed
        assert "holds the thesis" in narrowed

    async def test_the_knowledge_page_counts_the_judgement_layer(self, api: Any) -> None:
        body = (await api.get("/knowledge")).text
        assert 'id="stat-theses">2<' in body
        assert 'id="stat-premises">2<' in body
        assert 'id="stat-decisions">1<' in body
        assert 'id="stat-verdicts">0<' in body
