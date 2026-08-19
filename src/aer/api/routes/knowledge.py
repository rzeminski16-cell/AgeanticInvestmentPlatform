"""The knowledge graph's own measurements, as JSON.

`docs/knowledge-graph.md` task K5. A JSON endpoint rather than only a page, because the
figures worth having are the ones you can watch move: the stub ratio, the number of
approved reports nobody exported, the count of companies whose research has gone stale.
Those are trends, and a trend needs a machine-readable reading.

Not scoped per user. The graph is the installation's, not an account's — it is built from
every approved report in the database, and a per-user view of a shared graph would report
a connectivity that does not exist. Authentication is still required; this says what the
platform knows, which is not public.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from aer.api.deps import CurrentUser, DbSession, SettingsDep
from aer.services.knowledge import knowledge_stats

__all__ = ["router"]

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


@router.get("", summary="Measurements of the knowledge graph")
async def read_knowledge(
    session: DbSession,
    settings: SettingsDep,
    user: CurrentUser,  # noqa: ARG001 -- authentication, not filtering; see the module docstring
) -> dict[str, Any]:
    """Size, shape, coverage, freshness and vault health, measured now."""
    stats = await knowledge_stats(session, settings=settings)
    return stats.as_dict()
