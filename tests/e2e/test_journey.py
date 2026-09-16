"""One test per stopped state: does the interface offer a way forward, and does it work?

The rows come from `tests/journey_inventory.py`; the construction and the three assertions
from `tests/e2e/journey.py`. A row in `STILL_RED` is expected to fail on a `DeadEndError`
and on nothing else; a row in `UNCONSTRUCTED` is expected to fail with "no path constructed"
and on nothing else; both are strict, so a fix that works flips a row and a fix that does
not fails the build. Every other row must pass. The harness is green when `STILL_RED` is
empty.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page

from tests.e2e.journey import DeadEndError, NoPathConstructedError, Scene, build, check
from tests.journey_inventory import STILL_RED, UNCONSTRUCTED, Family, StoppedState, inventory

pytestmark = [pytest.mark.e2e, pytest.mark.integration]

ROWS = inventory()


def _marks(state: StoppedState) -> list[pytest.MarkDecorator]:
    if state.key in UNCONSTRUCTED:
        reason = f"no path constructed: {UNCONSTRUCTED[state.key]}"
        return [pytest.mark.xfail(strict=True, raises=NoPathConstructedError, reason=reason)]
    if state.key in STILL_RED:
        return [pytest.mark.xfail(strict=True, raises=DeadEndError, reason=STILL_RED[state.key])]
    return []


@pytest.fixture
def journey_env(
    request: pytest.FixtureRequest, settings_env: pytest.MonkeyPatch
) -> pytest.MonkeyPatch:
    """The ceilings a budget row needs, set before the server and the worker read them."""
    state: StoppedState = request.node.callspec.params["state"]
    if state.family is Family.BUDGET:
        detail = state.detail or ""
        if detail.endswith(":at_ceiling"):
            settings_env.setenv("AER_PER_RUN_BUDGET_GBP", "2.00")
        if detail == "monthly":
            settings_env.setenv("AER_MONTHLY_BUDGET_GBP", "1.00")
    return settings_env


@pytest.mark.parametrize(
    "state", [pytest.param(row, id=row.key, marks=_marks(row)) for row in ROWS]
)
def test_every_stopped_state_offers_a_way_forward(
    state: StoppedState,
    journey_env: pytest.MonkeyPatch,
    page: Page,
    live_server: str,
    database_url: str,
) -> None:
    del journey_env  # ordered before the server on purpose; its work is done
    scene = Scene(page=page, live_server=live_server, database_url=database_url)
    job_id = build(state, scene)
    check(state, scene, job_id)
