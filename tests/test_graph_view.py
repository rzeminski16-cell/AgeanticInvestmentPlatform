"""The in-app picture of the knowledge graph (docs/archive/knowledge-graph.md, K4b).

Two halves. The layout is pure arithmetic, so it is held directly: the same input always
draws the same picture, nodes never coincide, every line ends on a node, and the
symmetric relation arriving in both directions is drawn once. The assembly is a read of
confirmed rows, so it is held against a seeded scene: the node universe is exactly the
one the statistics count, a stub is hollow, a theme is its own node with a spoke per
membership, and nothing unapproved is drawn — the same refusal every other projection
makes.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import date
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aer.config import Settings
from aer.db.models import Theme, ThemeMembership
from aer.services.graph_view import GraphEdge, GraphNode, graph_picture, place
from tests.api_fixtures import build_app, client_for
from tests.test_knowledge_stats import _company, _run, _user

pytestmark = pytest.mark.anyio

AS_OF = date(2026, 6, 30)
_TABLES = "research_requests, audit_events, users, artefacts, companies, themes"


def _node(label: str, *, kind: str = "company", researched: bool = True) -> GraphNode:
    """A deterministic node: uuid5 keeps ids — and so the drawing — stable across runs."""
    return GraphNode(
        id=uuid.uuid5(uuid.NAMESPACE_URL, label),
        kind=kind,  # type: ignore[arg-type]
        label=label,
        title=label,
        researched=researched,
        href=None,
    )


class TestTheLayout:
    def test_the_same_input_always_draws_the_same_picture(self) -> None:
        nodes = [_node("AAA"), _node("BBB"), _node("CCC")]
        edges = [GraphEdge(a=nodes[0].id, b=nodes[1].id, kind="comparable")]

        assert place(list(nodes), list(edges)) == place(list(nodes), list(edges))

    def test_no_two_nodes_share_coordinates(self) -> None:
        nodes = [_node(f"N{index:02d}") for index in range(9)]
        edges = [GraphEdge(a=nodes[0].id, b=other.id, kind="comparable") for other in nodes[1:]]

        picture = place(nodes, edges)

        points = {(placed.x, placed.y) for placed in picture.nodes}
        assert len(points) == len(nodes)

    def test_every_node_sits_inside_the_canvas(self) -> None:
        nodes = [_node(f"N{index:02d}") for index in range(14)]

        picture = place(nodes, [])

        for placed in picture.nodes:
            assert 0 <= placed.x <= picture.width
            assert 0 <= placed.y <= picture.height

    def test_singletons_wrap_into_rows_rather_than_widening_forever(self) -> None:
        nodes = [_node(f"N{index:02d}") for index in range(14)]

        picture = place(nodes, [])

        assert picture.width <= 1180.0
        # Fourteen isolated boxes cannot fit one row, so a second row must exist.
        rows = {placed.y for placed in picture.nodes}
        assert len(rows) == 2

    def test_a_symmetric_edge_arriving_twice_is_drawn_once(self) -> None:
        alpha, beta = _node("AAA"), _node("BBB")
        both_directions = [
            GraphEdge(a=alpha.id, b=beta.id, kind="comparable"),
            GraphEdge(a=beta.id, b=alpha.id, kind="comparable"),
        ]

        picture = place([alpha, beta], both_directions)

        assert len(picture.edges) == 1

    def test_every_line_ends_on_a_node(self) -> None:
        nodes = [_node("AAA"), _node("BBB"), _node("CCC")]
        edges = [
            GraphEdge(a=nodes[0].id, b=nodes[1].id, kind="comparable"),
            GraphEdge(a=nodes[1].id, b=nodes[2].id, kind="membership"),
        ]

        picture = place(nodes, edges)

        points = {(placed.x, placed.y) for placed in picture.nodes}
        for line in picture.edges:
            assert (line.x1, line.y1) in points
            assert (line.x2, line.y2) in points

    def test_a_lone_node_is_centred_in_its_box(self) -> None:
        picture = place([_node("AAA")], [])

        assert len(picture.nodes) == 1
        assert picture.nodes[0].x == picture.width / 2
        assert picture.nodes[0].y == picture.height / 2


async def _confirmed_theme(
    session: AsyncSession, *, label: str, key: str, memberships: list[tuple[Any, Any]]
) -> Theme:
    """A theme with membership rows; whether they count is the report's approval."""
    theme = Theme(key=key, label=label)
    session.add(theme)
    await session.flush()
    for company, report in memberships:
        session.add(
            ThemeMembership(
                theme_id=theme.id,
                company_id=company.id,
                report_id=report.id,
                rationale="Named by the run.",
            )
        )
    await session.flush()
    return theme


@pytest.fixture
async def scene(db_session: AsyncSession) -> dict[str, Any]:
    """The knowledge-stats scene plus a confirmed theme bridging its two components."""
    await db_session.execute(text(f"TRUNCATE {_TABLES} RESTART IDENTITY CASCADE"))
    user = await _user(db_session)
    alpha = await _company(db_session, "ALPH", "Alpha plc")
    beta = await _company(db_session, "BETA", "Beta Inc")
    stub = await _company(db_session, "STUB", "Stub Corporation")
    lone = await _company(db_session, "LONE", "Lone Holdings")

    report_alpha = await _run(db_session, user=user, company=alpha, peers=[beta, stub])
    await _run(db_session, user=user, company=beta, peers=[alpha])
    report_lone = await _run(db_session, user=user, company=lone)
    draft = await _run(db_session, user=user, company=lone, approved=False)

    theme = await _confirmed_theme(
        db_session,
        label="AI capital expenditure",
        key="ai-capex",
        memberships=[(alpha, report_alpha), (lone, report_lone)],
    )
    await _confirmed_theme(
        db_session, label="Draft only", key="draft-only", memberships=[(lone, draft)]
    )
    return {
        "session": db_session,
        "user": user,
        "alpha": alpha,
        "beta": beta,
        "stub": stub,
        "lone": lone,
        "theme": theme,
    }


class TestTheAssembly:
    async def test_the_picture_draws_the_universe_the_statistics_count(
        self, scene: dict[str, Any]
    ) -> None:
        picture = await graph_picture(scene["session"])

        drawn = {placed.node.label: placed.node for placed in picture.nodes}
        assert set(drawn) == {"ALPH", "BETA", "STUB", "LONE", "AI capital expenditure"}
        assert drawn["STUB"].researched is False
        assert drawn["ALPH"].researched is True
        assert drawn["ALPH"].href == f"/companies/{scene['alpha'].id}"
        assert drawn["AI capital expenditure"].kind == "theme"
        assert drawn["AI capital expenditure"].href is None

    async def test_comparable_edges_are_drawn_once_each(self, scene: dict[str, Any]) -> None:
        """Alpha↔Beta and Alpha↔Stub: two lines, however many directions the rows hold."""
        picture = await graph_picture(scene["session"])

        assert sum(1 for edge in picture.edges if edge.kind == "comparable") == 2

    async def test_membership_spokes_join_the_theme_to_its_members(
        self, scene: dict[str, Any]
    ) -> None:
        picture = await graph_picture(scene["session"])

        assert sum(1 for edge in picture.edges if edge.kind == "membership") == 2

    async def test_a_membership_through_a_draft_is_not_drawn(self, scene: dict[str, Any]) -> None:
        """The same refusal the vault and the statistics make, pinned on the view path."""
        picture = await graph_picture(scene["session"])

        assert all(placed.node.label != "Draft only" for placed in picture.nodes)

    async def test_the_theme_bridges_what_it_connects(self, scene: dict[str, Any]) -> None:
        """LONE joins ALPH's picture only through the confirmed theme's spokes."""
        picture = await graph_picture(scene["session"])

        points = {(placed.x, placed.y) for placed in picture.nodes}
        for line in picture.edges:
            assert (line.x1, line.y1) in points
            assert (line.x2, line.y2) in points

    async def test_an_empty_graph_draws_nothing(self, db_session: AsyncSession) -> None:
        await db_session.execute(text(f"TRUNCATE {_TABLES} RESTART IDENTITY CASCADE"))

        picture = await graph_picture(db_session)

        assert picture.nodes == ()
        assert picture.edges == ()
        assert picture.width == 0.0


@pytest.mark.integration
class TestThePage:
    """The page, against a scene the application's own session sees (committed)."""

    @pytest.fixture
    async def committed(self, db_engine: Any) -> AsyncIterator[dict[str, Any]]:
        async with db_engine.begin() as connection:
            await connection.execute(text(f"TRUNCATE {_TABLES} RESTART IDENTITY CASCADE"))
        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as session:
            user = await _user(session)
            alpha = await _company(session, "ALPH", "Alpha plc")
            stub = await _company(session, "STUB", "Stub Corporation")
            report = await _run(session, user=user, company=alpha, peers=[stub])
            await _confirmed_theme(
                session, label="AI capex", key="ai-capex", memberships=[(alpha, report)]
            )
            await session.commit()
        try:
            yield {"alpha": alpha, "stub": stub}
        finally:
            async with db_engine.begin() as connection:
                await connection.execute(text(f"TRUNCATE {_TABLES} RESTART IDENTITY CASCADE"))

    @pytest.fixture
    async def api(
        self, api_settings: Settings, db_engine: Any, fake_redis: Any, committed: dict[str, Any]
    ) -> AsyncIterator[Any]:
        async for client in client_for(build_app(api_settings, engine=db_engine, redis=fake_redis)):
            yield client

    async def test_the_page_renders_the_svg_with_links_to_the_companies(
        self, api: Any, committed: dict[str, Any]
    ) -> None:
        response = await api.get("/knowledge/graph")

        assert response.status_code == 200, response.text
        assert 'id="graph-picture"' in response.text
        assert f"/companies/{committed['alpha'].id}" in response.text
        assert "AI capex" in response.text

    async def test_the_knowledge_page_links_to_the_drawing(self, api: Any) -> None:
        response = await api.get("/knowledge")

        assert response.status_code == 200, response.text
        assert 'id="knowledge-graph-link"' in response.text

    async def test_every_relation_also_exists_in_words(
        self, api: Any, committed: dict[str, Any]
    ) -> None:
        """Tranche 8: the drawing gains an adjacent relation list built from the same
        placed edges, so the topology is readable by somebody who cannot perceive it —
        and checkable by anybody who doubts a line."""
        response = await api.get("/knowledge/graph")

        assert response.status_code == 200, response.text
        assert 'id="relation-list"' in response.text
        assert "is comparable to" in response.text or "is a member of" in response.text

    async def test_an_empty_graph_explains_itself(
        self, api_settings: Settings, db_engine: Any, fake_redis: Any
    ) -> None:
        """A user with no approved research yet: the page explains rather than draws."""
        async with db_engine.begin() as connection:
            await connection.execute(text(f"TRUNCATE {_TABLES} RESTART IDENTITY CASCADE"))
        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as session:
            await _user(session)
            await session.commit()

        async for client in client_for(build_app(api_settings, engine=db_engine, redis=fake_redis)):
            response = await client.get("/knowledge/graph")
            assert response.status_code == 200, response.text
            assert 'id="graph-empty"' in response.text
