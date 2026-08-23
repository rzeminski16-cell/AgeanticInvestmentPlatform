"""The Overview screen.

The second surface in the product, and the first one that is not the research tool's. It
holds no query of its own: the counts are every registered badge (`web/shell/badges.py`)
and the feed is every registered attention provider (`attention.py`), so a tool added later
appears here by adding a row to a registry rather than a branch to this template.

One number is genuinely the platform's rather than a tool's — what has been spent this
month — and it is here because metering is the kernel's (ADR 0053) and nothing else on the
page is money.

**No provenance badge, and that is a decision rather than an omission.** ADR 0054 defines a
figure as a numeral denoting a quantity, which invariant 3 requires to be a stored fact or
a recorded calculation. Nothing on this screen is one: a count of stopped runs is a count of
rows, and the month's spend is a sum of metered charges the platform itself wrote. Putting a
"Calculated" badge beside either would spend the vocabulary that means "this traces to a
formula" on something that does not.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Final

from fastapi import APIRouter, Request
from starlette.responses import HTMLResponse, Response

from aer.api.deps import CurrentUser, DbSession, RedisClient
from aer.core.dates import format_date
from aer.services.overview import spend_since, start_of_month
from aer.web.overview.attention import Severity, items_for
from aer.web.shell.badges import cached_counts_for
from aer.web.templating import render

__all__ = ["router"]

router = APIRouter(include_in_schema=False)

# The order the feed is grouped into on the page. Held here rather than derived from the
# enum's declaration order, because a reordering of the vocabulary should not silently
# reorder a screen.
SEVERITY_ORDER: Final[tuple[Severity, ...]] = (
    Severity.BLOCKED,
    Severity.BROKEN,
    Severity.IDLE,
)


@router.get("/overview", response_class=HTMLResponse, summary="What is waiting for you")
async def overview_page(
    request: Request,
    session: DbSession,
    redis: RedisClient,
    user: CurrentUser,
) -> Response:
    """Everything waiting, from whichever tool it is waiting in.

    The clock is read here rather than in the service, so `services/overview.py` stays free
    of clock reads and a test can ask for a specific month without moving the machine's.
    """
    now = datetime.now(UTC)
    since = start_of_month(now)
    badges = await cached_counts_for(redis, session, user_id=user.id)
    attention = await items_for(session, user_id=user.id)

    page: Response = render(
        request,
        "overview/index.html",
        {
            "badges": badges,
            "spend": _pounds(await spend_since(session, since=since)),
            # Rendered here rather than in the template: `%-d` does not exist outside
            # glibc, and `format_date` is the one place that knows it.
            "since": f"Since {format_date(since, '%-d %B')}",
            "attention": attention,
            "severity_order": SEVERITY_ORDER,
            "blocked": Severity.BLOCKED,
            "broken": Severity.BROKEN,
            "idle": Severity.IDLE,
        },
    )
    return page


def _pounds(amount: Decimal) -> str:
    """Operator spend, in pounds, rendered here rather than in a template.

    Not through `render/display.money`: that door exists for a company's figures in a
    report's own currency, resolved against a `HouseStyle` (ADR 0056), and this screen has
    no report and no house style. What it shares with that door is the rule that the digits
    are decided in Python — `_ui/surfaces.html` takes a rendered value precisely so that a
    macro cannot become a second place formatting is decided.

    A total that rounds to nothing says so rather than showing ``£0.00``, because "we have
    spent nothing this month" and "we have spent a third of a penny" are different answers
    and only one of them is true.
    """
    if amount and amount < Decimal("0.01"):
        return "under £0.01"
    return f"£{amount:,.2f}"
