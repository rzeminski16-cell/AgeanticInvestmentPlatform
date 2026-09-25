"""The browser suite queues its runs on its own Redis database (roadmap §3.19 item 71).

Every run a browser test commissioned used to be enqueued on database 0 — the operator's
queue, the one a real worker reads — where nothing took it until the next real worker did.
On the verdict round's first day that was 1,225 of them, and AZN waited ten minutes behind
jobs nobody had commissioned. `worker.py` in this directory says there is no queue in these
tests; this is what makes that true of the operator's.
"""

from __future__ import annotations

import re

import pytest
import redis
from playwright.sync_api import Page

from tests.e2e.conftest import E2E_REDIS_URL
from tests.e2e.test_research_journey import CONSOLE_URL, _commission, _job_id

pytestmark = [pytest.mark.e2e, pytest.mark.integration]

# What a worker started with no configuration reads: the platform's default Redis URL.
OPERATOR_QUEUE = "redis://127.0.0.1:6379/0"


def _jobs_naming(url: str, job_id: str) -> int:
    """How many queued arq jobs on this database carry the run's id in their arguments."""
    client = redis.Redis.from_url(url)
    try:
        return sum(
            1
            for key in client.scan_iter(match="arq:job:*")
            if job_id.encode() in (client.get(key) or b"")
        )
    finally:
        client.close()


def test_a_run_started_in_the_browser_waits_on_the_suite_s_own_queue(
    page: Page, live_server: str, database_url: str
) -> None:
    _commission(page, live_server)
    page.wait_for_url(re.compile(r"/requests/[0-9a-f-]{36}$"))
    page.click("#start-run")
    page.wait_for_url(CONSOLE_URL)
    job_id = str(_job_id(database_url))

    assert _jobs_naming(E2E_REDIS_URL, job_id) == 1, "the run is queued, on the suite's queue"
    assert _jobs_naming(OPERATOR_QUEUE, job_id) == 0, "and not on the operator's"
