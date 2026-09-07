"""One mandate and the run root it is a detail of, built together.

ADR 0072 made `work_orders` the run root and its follow-up revision dropped the columns
that had been duplicated onto `research_requests` for the transition. So a mandate no
longer carries who asked, what the run may spend, what date its evidence is judged against
or whether it is archived — those are the run's, and exactly one row holds them.

Seventy fixtures build a request directly, because what they test is what happens *after*
one exists, and routing every one through `create_request` would be testing the service
seventy times over. This splits their keyword arguments across the two rows the way the
service does, so a fixture keeps meaning what it meant.

**It cannot mask a production regression**, which is the condition for it being acceptable
rather than convenient: `TestCreateRequest::test_it_creates_the_work_order_it_hangs_off` in
`tests/test_request_api.py` asserts the service builds both rows itself.

**Its own module rather than `conftest`**, because the fixture modules `conftest` imports
need it, and a helper living where they import from is a circular import.
"""

from __future__ import annotations

import uuid
from typing import Any

from aer.db.models import ResearchRequest, WorkOrder

__all__ = ["research_request"]

# What belongs to the run rather than to the equity report.
_RUN_ROOT_FIELDS = frozenset(
    {"user_id", "as_of_date", "point_in_time", "max_cost_gbp", "status", "archived_at"}
)


def research_request(**fields: Any) -> ResearchRequest:
    """A `ResearchRequest` and its `WorkOrder`, sharing a key, ready to be added.

    Adding the request cascades the work order with it, so a caller's `session.add(request)`
    still writes both rows in one flush.
    """
    identifier = fields.pop("id", None) or uuid.uuid4()
    root = {name: fields.pop(name) for name in list(fields) if name in _RUN_ROOT_FIELDS}
    order = WorkOrder(
        id=identifier,
        tool="research",
        subject_kind="company",
        subject_id=fields.get("company_id"),
        **root,
    )
    return ResearchRequest(id=identifier, work_order=order, **fields)
