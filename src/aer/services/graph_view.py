"""The knowledge graph drawn in the application (docs/knowledge-graph.md, K4b).

:mod:`aer.services.knowledge` measures the graph; this module draws it. The picture is
computed here, server-side, as coordinates for a static SVG: the layout is arithmetic
over the same confirmed relations the vault projects — no model call takes part, no
script runs in the browser, and two renders of the same rows place every node at the
same coordinates, which is what lets a test hold the drawing rather than eyeball it.

The node universe is exactly the one the statistics count — every company in the
confirmed comparable relation or carrying approved research — plus each theme with a
confirmed member, drawn as its own node with a spoke per membership. Themes are nodes
rather than pairwise company edges because ``Company ↔ Theme`` is the stored relation;
a clique drawn per theme would invent edges nobody confirmed individually.

The layout is deliberately naive: each connected component on its own circle, components
packed into rows, largest first. For a personal research library measured in tens of
companies that is legible; anything cleverer (force simulation, crossing minimisation)
buys prettiness with nondeterminism, and Obsidian's own view already exists for the
pretty picture.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.db.models import Company, Report, Theme, ThemeMembership
from aer.obsidian.graph import peer_edges, reachable_from
from aer.services.knowledge import researched_companies

__all__ = [
    "GraphEdge",
    "GraphNode",
    "GraphPicture",
    "PlacedEdge",
    "PlacedNode",
    "graph_picture",
    "place",
]

NodeKind = Literal["company", "theme"]
EdgeKind = Literal["comparable", "membership"]

# Arc length per node on a component's circle: enough for a marker and a label below it.
_NODE_SPACING = 96.0
_MIN_RADIUS = 70.0
# Margin around each component's circle, so labels never cross into the neighbour's box.
_BOX_PADDING = 56.0
_MAX_ROW_WIDTH = 1180.0


@dataclass(frozen=True, slots=True)
class GraphNode:
    """One node before layout: identity, what to print, and where clicking goes."""

    id: uuid.UUID
    kind: NodeKind
    label: str
    title: str
    researched: bool
    href: str | None


@dataclass(frozen=True, slots=True)
class GraphEdge:
    """One confirmed relation between two nodes, before layout."""

    a: uuid.UUID
    b: uuid.UUID
    kind: EdgeKind


@dataclass(frozen=True, slots=True)
class PlacedNode:
    node: GraphNode
    x: float
    y: float


@dataclass(frozen=True, slots=True)
class PlacedEdge:
    x1: float
    y1: float
    x2: float
    y2: float
    kind: EdgeKind


@dataclass(frozen=True, slots=True)
class GraphPicture:
    """A finished drawing: canvas extent, every node placed, every edge as a line."""

    width: float
    height: float
    nodes: tuple[PlacedNode, ...]
    edges: tuple[PlacedEdge, ...]


def _sort_key(node: GraphNode) -> tuple[int, str, str]:
    # Companies before themes, then by label; the id breaks a duplicate-label tie so the
    # order — and therefore the whole drawing — never depends on set iteration.
    return (0 if node.kind == "company" else 1, node.label.casefold(), str(node.id))


def place(nodes: list[GraphNode], edges: list[GraphEdge]) -> GraphPicture:
    """Lay the graph out deterministically: one circle per component, packed into rows.

    Pure arithmetic — the same nodes and edges always produce the same picture. Edges are
    deduplicated here (the comparable relation arrives in both directions) and every line
    ends exactly on a node's coordinates, so the drawing cannot show a relation the
    caller did not pass.
    """
    by_id = {node.id: node for node in nodes}
    adjacency: dict[uuid.UUID, set[uuid.UUID]] = {}
    for edge in edges:
        adjacency.setdefault(edge.a, set()).add(edge.b)
        adjacency.setdefault(edge.b, set()).add(edge.a)

    # Components by the same reachability walk the exporter and the statistics use.
    remaining = set(by_id)
    components: list[list[GraphNode]] = []
    while remaining:
        seed = min(remaining, key=lambda node_id: _sort_key(by_id[node_id]))
        found = reachable_from({seed}, adjacency) & set(by_id)
        remaining -= found
        components.append(sorted((by_id[node_id] for node_id in found), key=_sort_key))
    components.sort(key=lambda component: (-len(component), _sort_key(component[0])))

    positions: dict[uuid.UUID, tuple[float, float]] = {}
    x_cursor = 0.0
    y_cursor = 0.0
    row_height = 0.0
    width = 0.0
    for members in components:
        count = len(members)
        radius = 0.0 if count == 1 else max(_MIN_RADIUS, count * _NODE_SPACING / math.tau)
        side = 2 * (radius + _BOX_PADDING)
        if x_cursor > 0 and x_cursor + side > _MAX_ROW_WIDTH:
            y_cursor += row_height
            x_cursor = 0.0
            row_height = 0.0
        centre_x = x_cursor + side / 2
        centre_y = y_cursor + side / 2
        for index, node in enumerate(members):
            angle = -math.pi / 2 + math.tau * index / count
            positions[node.id] = (
                round(centre_x + radius * math.cos(angle), 1),
                round(centre_y + radius * math.sin(angle), 1),
            )
        x_cursor += side
        row_height = max(row_height, side)
        width = max(width, x_cursor)

    placed_nodes = tuple(
        PlacedNode(node=node, x=positions[node.id][0], y=positions[node.id][1])
        for members in components
        for node in members
    )

    drawn: set[tuple[uuid.UUID, uuid.UUID, EdgeKind]] = set()
    lines: list[PlacedEdge] = []
    for edge in sorted(edges, key=lambda e: (e.kind, *sorted((str(e.a), str(e.b))))):
        first, second = sorted((edge.a, edge.b), key=str)
        key = (first, second, edge.kind)
        if key in drawn or first == second:
            continue
        drawn.add(key)
        (x1, y1), (x2, y2) = positions[first], positions[second]
        lines.append(PlacedEdge(x1=x1, y1=y1, x2=x2, y2=y2, kind=edge.kind))

    return GraphPicture(
        width=round(width, 1),
        height=round(y_cursor + row_height, 1),
        nodes=placed_nodes,
        edges=tuple(lines),
    )


async def graph_picture(session: AsyncSession) -> GraphPicture:
    """Assemble the drawing from rows: confirmed relations only, like every projection.

    A proposed-but-unapproved peer set, a theme membership through a draft report, a
    company nobody confirmed anything about — none of them appear, for the same reason
    they produce no vault note and no statistic.
    """
    peers = await peer_edges(session)
    researched = await researched_companies(session)
    company_ids = set(peers) | set(researched)

    stub_ids = company_ids - set(researched)
    stubs: dict[uuid.UUID, Company] = {}
    if stub_ids:
        rows = await session.scalars(select(Company).where(Company.id.in_(stub_ids)))
        stubs = {row.id: row for row in rows}

    nodes: list[GraphNode] = []
    for company_id in company_ids:
        company = researched.get(company_id) or stubs[company_id]
        nodes.append(
            GraphNode(
                id=company.id,
                kind="company",
                label=company.ticker or company.name,
                title=(f"{company.ticker} — {company.name}" if company.ticker else company.name),
                researched=company_id in researched,
                href=f"/companies/{company.id}",
            )
        )

    theme_rows = await session.execute(
        select(Theme, ThemeMembership.company_id)
        .join(ThemeMembership, ThemeMembership.theme_id == Theme.id)
        .join(Report, Report.id == ThemeMembership.report_id)
        .where(Report.immutable.is_(True))
        .distinct()
    )
    themes: dict[uuid.UUID, Theme] = {}
    spokes: set[tuple[uuid.UUID, uuid.UUID]] = set()
    for theme, member_id in theme_rows:
        themes[theme.id] = theme
        spokes.add((theme.id, member_id))
    nodes.extend(
        GraphNode(
            id=theme.id,
            kind="theme",
            label=theme.label,
            title=theme.label,
            researched=True,
            href=None,
        )
        for theme in themes.values()
    )

    edges: list[GraphEdge] = []
    for node_id, neighbours in peers.items():
        edges.extend(
            GraphEdge(a=node_id, b=neighbour, kind="comparable") for neighbour in neighbours
        )
    edges.extend(
        GraphEdge(a=theme_id, b=member_id, kind="membership") for theme_id, member_id in spokes
    )

    return place(sorted(nodes, key=_sort_key), edges)
