"""The knowledge graph drawn in the application (docs/archive/knowledge-graph.md, K4b).

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
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from typing import Final, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.db.models import Company, Report, Theme, ThemeMembership
from aer.obsidian.graph import peer_edges, reachable_from
from aer.obsidian.judgements import judgement_views, thesis_subjects
from aer.services.decisions import ACTION_WORDS
from aer.services.knowledge import researched_companies

__all__ = [
    "KINDS",
    "GraphEdge",
    "GraphNode",
    "GraphPicture",
    "PlacedEdge",
    "PlacedNode",
    "graph_picture",
    "place",
]

NodeKind = Literal["company", "theme", "thesis", "premise", "decision", "verdict"]
EdgeKind = Literal["comparable", "membership", "holds", "asserts", "acts_on", "scores"]

# Every kind the picture can draw, in the order the legend lists them and the layout
# ranks them: what the platform researched first, then what the operator decided (ADR
# 0122). The page filters by kind from its first version, because the judgement nodes
# outnumber the research nodes within a year of ordinary use.
KINDS: Final[tuple[NodeKind, ...]] = (
    "company",
    "theme",
    "thesis",
    "premise",
    "decision",
    "verdict",
)
_RANK: Final[dict[str, int]] = {kind: index for index, kind in enumerate(KINDS)}

# Arc length per node on a component's circle: enough for a marker and a label below it.
_NODE_SPACING = 96.0
_MIN_RADIUS = 70.0
# Margin around each component's circle, so labels never cross into the neighbour's box.
_BOX_PADDING = 56.0
_MAX_ROW_WIDTH = 1180.0
# A label is a handle, not the text: a premise is a sentence, and the sentence is the title.
_LABEL_WIDTH = 28


@dataclass(frozen=True, slots=True)
class GraphNode:
    """One node before layout: identity, what to print, and where clicking goes."""

    id: uuid.UUID
    kind: NodeKind
    label: str
    title: str
    researched: bool
    href: str | None
    # What state the node is in, in the record's own words: a premise's latest reading
    # (or "withdrawn"), a decision's action, a verdict's process quality, a thesis held or
    # retired. Empty for the research kinds, whose state is `researched`.
    state: str = ""


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

    # The endpoints by name, so the page can list every relation in words beside the
    # drawing — the graph has to be usable by a reader who cannot perceive the topology,
    # and a list derived here cannot disagree with the lines it sits next to.
    a_label: str = ""
    b_label: str = ""


@dataclass(frozen=True, slots=True)
class GraphPicture:
    """A finished drawing: canvas extent, every node placed, every edge as a line."""

    width: float
    height: float
    nodes: tuple[PlacedNode, ...]
    edges: tuple[PlacedEdge, ...]
    # How many nodes of each kind the rows hold, before any filter — the legend's numbers —
    # and which kinds this drawing shows.
    counts: dict[str, int] = field(default_factory=dict)
    shown: tuple[str, ...] = KINDS


def _sort_key(node: GraphNode) -> tuple[int, str, str]:
    # Kinds in the legend's order, then by label; the id breaks a duplicate-label tie so
    # the order — and therefore the whole drawing — never depends on set iteration.
    return (_RANK[node.kind], node.label.casefold(), str(node.id))


def _short(text: str) -> str:
    return text if len(text) <= _LABEL_WIDTH else text[: _LABEL_WIDTH - 1].rstrip() + "…"


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
        lines.append(
            PlacedEdge(
                x1=x1,
                y1=y1,
                x2=x2,
                y2=y2,
                kind=edge.kind,
                a_label=by_id[first].label,
                b_label=by_id[second].label,
            )
        )

    return GraphPicture(
        width=round(width, 1),
        height=round(y_cursor + row_height, 1),
        nodes=placed_nodes,
        edges=tuple(lines),
    )


async def graph_picture(
    session: AsyncSession, *, kinds: Iterable[str] | None = None
) -> GraphPicture:
    """Assemble the drawing from rows: confirmed relations only, like every projection.

    A proposed-but-unapproved peer set, a theme membership through a draft report, a
    company nobody confirmed anything about — none of them appear, for the same reason
    they produce no vault note and no statistic. ``kinds`` narrows what is drawn to those
    node kinds (an edge needs both its ends); the counts in the legend are of everything.
    """
    peers = await peer_edges(session)
    researched = await researched_companies(session)
    # A company somebody holds a thesis on is in the map (ADR 0122), researched or not.
    subjects = await thesis_subjects(session)
    company_ids = set(peers) | set(researched) | set(subjects)

    known: dict[uuid.UUID, Company] = {**subjects, **researched}
    stub_ids = company_ids - set(known)
    if stub_ids:
        rows = await session.scalars(select(Company).where(Company.id.in_(stub_ids)))
        known.update({row.id: row for row in rows})

    nodes: list[GraphNode] = []
    for company_id in company_ids:
        company = known[company_id]
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

    # The judgement layer (ADR 0122 §1): four kinds and four edges, the installation's
    # like everything else drawn here. A premise carries its latest reading as its state
    # rather than a node per finding; a verdict is a review the operator confirmed.
    for view in await judgement_views(session, companies=known):
        thesis = view.thesis
        nodes.append(
            GraphNode(
                id=thesis.id,
                kind="thesis",
                label=_short(thesis.title),
                title=thesis.title,
                researched=not thesis.is_retired,
                href=f"/theses/{thesis.id}",
                state="retired" if thesis.is_retired else "held",
            )
        )
        edges.append(GraphEdge(a=view.company.id, b=thesis.id, kind="holds"))
        for premise_view in view.premises:
            premise = premise_view.premise
            reading = premise_view.reading
            if premise_view.is_withdrawn:
                state = "withdrawn"
            elif reading is not None and reading.status is not None:
                state = reading.status.value
            else:
                state = "unread"
            nodes.append(
                GraphNode(
                    id=premise.judgement_id,
                    kind="premise",
                    label=_short(premise.statement),
                    title=premise.statement,
                    researched=not premise_view.is_withdrawn,
                    href=f"/theses/{thesis.id}",
                    state=state,
                )
            )
            edges.append(GraphEdge(a=thesis.id, b=premise.judgement_id, kind="asserts"))
        for decision_view in view.decisions:
            decision = decision_view.decision
            nodes.append(
                GraphNode(
                    id=decision.judgement_id,
                    kind="decision",
                    label=ACTION_WORDS[decision.action],
                    title=(
                        f"{decision.judgement.held_at.date().isoformat()}: {decision.statement}"
                    ),
                    researched=decision.judgement.withdrawn_at is None,
                    href=f"/decisions/{decision.judgement_id}",
                    state=decision.action.value,
                )
            )
            edges.append(GraphEdge(a=decision.judgement_id, b=thesis.id, kind="acts_on"))
            if decision_view.verdict is not None:
                edges.append(
                    GraphEdge(
                        a=decision_view.verdict.judgement_id,
                        b=decision.judgement_id,
                        kind="scores",
                    )
                )
        nodes.extend(
            GraphNode(
                id=review.judgement_id,
                kind="verdict",
                label=f"{review.process_quality.value} process",
                title=f"Closed {review.closed_on.isoformat()}: {review.judgement.basis}",
                researched=True,
                href=f"/review/{review.judgement_id}",
                state=review.process_quality.value,
            )
            for review in view.verdicts
        )

    counts: dict[str, int] = {kind: sum(1 for node in nodes if node.kind == kind) for kind in KINDS}
    wanted = set(kinds) if kinds is not None else set(KINDS)
    shown = tuple(kind for kind in KINDS if kind in wanted) or KINDS
    if shown != KINDS:
        keep = {node.id for node in nodes if node.kind in shown}
        nodes = [node for node in nodes if node.id in keep]
        edges = [edge for edge in edges if edge.a in keep and edge.b in keep]

    picture = place(sorted(nodes, key=_sort_key), edges)
    return replace(picture, counts=counts, shown=shown)
