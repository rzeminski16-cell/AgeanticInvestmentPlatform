"""What the platform itself has waiting, which is not a tool's business.

One item today: the database being behind the models. It is here rather than in the
research tool's provider because it is true of every tool at once — a missing column is not
a research problem — and because the moment a second tool exists, an operator seeing it
listed under "Research" would go looking in the wrong place.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from aer.db.schema_check import schema_drift
from aer.web.overview.attention import Attention, Severity

if TYPE_CHECKING:
    import uuid
    from collections.abc import Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

__all__ = ["items"]

TOOL: Final = "platform"


async def items(session: AsyncSession, *, user_id: uuid.UUID) -> Sequence[Attention]:
    """Whatever is wrong with the platform rather than with a piece of work.

    ``user_id`` is unused and stays in the signature: every provider answers the same
    question and one with a different shape would make the registry's reference a thing
    each caller had to know the arity of.
    """
    del user_id  # the schema is the same schema for everybody

    drift = await schema_drift(session)
    if drift.is_clean:
        return ()
    return (
        Attention(
            key="platform.schema_drift",
            tool=TOOL,
            severity=Severity.BROKEN,
            title="The database is behind the models",
            # `as_message` names the objects rather than the count, which is what tells an
            # operator which migration they skipped.
            detail=drift.as_message(),
            href="/healthz",
            action="Check the platform",
        ),
    )
