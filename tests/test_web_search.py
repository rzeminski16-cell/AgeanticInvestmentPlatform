"""The web-search tool (ADR 0092): a listing, bounded, metered, and refused honestly.

Three layers. The **executor** owns every deterministic decision — the point-in-time
refusal, the per-worker bound, the unrouted refusal, the metering of both halves of the
bill — and is tested against the database with the fake provider. The **provider** owns
the deterministic read of the vendor's response — listing fields out, error objects
detected, ``pause_turn`` resumed — and is tested against a stubbed SDK client, the same
way the rest of the provider is. The **price** is the verified figure, and the test pins
it to the ADR's arithmetic so a drifted constant is a red build.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.agents.base import AgentContext
from aer.agents.worker import _TOOL_BRIEFS
from aer.core.enums import JobStatus
from aer.db.models import Cost, JobStep
from aer.errors import ExternalServiceError
from aer.providers.anthropic import AnthropicProvider
from aer.providers.costs import WEB_SEARCH_USD_PER_CALL, price_web_search
from aer.providers.fake import FakeProvider
from aer.providers.router import Router
from aer.services.research import MAX_WEB_SEARCHES, build_executors
from aer.workflow.workflows.vertical_slice_v1 import WORKFLOW_VERSION
from tests.workflow_fixtures import seed_job, seed_request, seed_user

pytestmark = pytest.mark.anyio


class _Request(SimpleNamespace):
    """The two fields a tool request carries into an executor."""


def _tool_request(query: str = "Contoso earnings announcement") -> Any:
    return _Request(tool="web_search", query=query, why="recent developments")


@pytest.fixture
async def scene(
    db_session: AsyncSession, workflow_settings: Any, workflow_store: Any
) -> dict[str, Any]:
    """A run mid-research-step, with a search-capable fake provider on the context."""
    user = await seed_user(db_session)
    request = await seed_request(db_session, user=user)
    # The live case: researching the present, where a search cannot leak the future.
    request.point_in_time = False
    job = await seed_job(db_session, request=request)
    step = JobStep(
        job_id=job.id,
        step_key="research_recent_developments",
        sequence=0,
        status=JobStatus.RUNNING,
        attempt=0,
        idempotency_key=f"{job.id}:research_recent_developments",
        input_hash="0" * 64,
        started_at=datetime.now(UTC),
    )
    db_session.add(step)
    await db_session.flush()

    provider = FakeProvider()
    context = AgentContext(
        session=db_session,
        provider=provider,
        router=Router(workflow_settings),
        settings=workflow_settings,
        store=workflow_store,
        job_step=step,
    )
    return {
        "session": db_session,
        "request": request,
        "job": job,
        "step": step,
        "provider": provider,
        "context": context,
        "settings": workflow_settings,
    }


def _executors(scene: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    return build_executors(
        scene["session"],
        request=scene["request"],
        job_id=scene["job"].id,
        agent_context=overrides.get("agent_context", scene["context"]),
    )


async def _costs(session: AsyncSession, step_id: Any) -> list[Cost]:
    return list(await session.scalars(select(Cost).where(Cost.job_step_id == step_id)))


class TestTheExecutor:
    async def test_a_search_returns_a_listing_wrapped_untrusted_at_t6(
        self, scene: dict[str, Any]
    ) -> None:
        outcome = await _executors(scene)["web_search"](_tool_request())

        assert outcome.executed
        assert scene["provider"].web_searches[0]["query"] == "Contoso earnings announcement"
        # The listing's note says what this is: colour, never evidence.
        assert outcome.internal_results[0]["results"] == len(outcome.untrusted_evidence)
        assert "not a reading" in outcome.internal_results[0]["note"]

        for item in outcome.untrusted_evidence:
            assert item["tier"] == "T6_UNVERIFIED"
            assert item["source_document_id"] == "web-search-result (not citable)"
            # Titles and URLs only — the fake's scripted hits carry no page text, and the
            # entry's text is built from the listing fields alone.
            assert "example.com" in item["text"]

    async def test_both_halves_of_the_bill_are_metered_against_the_step(
        self, scene: dict[str, Any]
    ) -> None:
        before = scene["context"].spend_gbp
        await _executors(scene)["web_search"](_tool_request())

        rows = await _costs(scene["session"], scene["step"].id)
        by_category = {row.category: row for row in rows}
        fee = by_category["web_search"]
        assert fee.units == Decimal(1)
        assert fee.unit_type == "searches"
        assert fee.amount_usd == WEB_SEARCH_USD_PER_CALL
        # The carrying call's tokens are priced beside the fee, not folded into it.
        assert "llm_input" in by_category
        assert scene["context"].spend_gbp > before

    async def test_the_bound_holds_and_the_refusal_names_it(self, scene: dict[str, Any]) -> None:
        executor = _executors(scene)["web_search"]
        for _ in range(MAX_WEB_SEARCHES):
            assert (await executor(_tool_request())).executed

        refused = await executor(_tool_request())

        assert not refused.executed
        assert str(MAX_WEB_SEARCHES) in refused.refusal
        assert len(scene["provider"].web_searches) == MAX_WEB_SEARCHES

    async def test_a_point_in_time_run_with_a_past_as_of_date_never_searches(
        self, scene: dict[str, Any]
    ) -> None:
        scene["request"].point_in_time = True
        # seed_request's as-of date is 2022-06-30 — deep in the past.
        refused = await _executors(scene)["web_search"](_tool_request())

        assert not refused.executed
        assert "point-in-time" in refused.refusal
        assert scene["provider"].web_searches == []
        assert await _costs(scene["session"], scene["step"].id) == []

    async def test_a_point_in_time_run_researching_the_present_may_search(
        self, scene: dict[str, Any]
    ) -> None:
        scene["request"].point_in_time = True
        scene["request"].as_of_date = (datetime.now(UTC) + timedelta(days=1)).date()

        outcome = await _executors(scene)["web_search"](_tool_request())

        assert outcome.executed

    async def test_a_failed_search_is_a_refusal_and_costs_nothing(
        self, scene: dict[str, Any]
    ) -> None:
        scene["provider"].web_search_error = "max_uses_exceeded"

        refused = await _executors(scene)["web_search"](_tool_request())

        assert not refused.executed
        assert "failed" in refused.refusal
        # The vendor does not bill an errored search, and neither does the meter.
        assert await _costs(scene["session"], scene["step"].id) == []

    async def test_an_unrouted_deployment_gets_a_refusal_not_a_default_model(
        self, scene: dict[str, Any]
    ) -> None:
        scene["context"].router._routes.pop("web_search")

        refused = await _executors(scene)["web_search"](_tool_request())

        assert not refused.executed
        assert "No model route" in refused.refusal
        assert scene["provider"].web_searches == []

    async def test_without_an_agent_context_the_tool_is_simply_not_bound(
        self, scene: dict[str, Any]
    ) -> None:
        executors = build_executors(
            scene["session"], request=scene["request"], job_id=scene["job"].id
        )
        assert "web_search" not in executors

    def test_the_worker_is_told_what_the_tool_is(self) -> None:
        # The brief map is pinned to the allowlist by the worker tests; this pins the
        # substance — a worker must be told the listing is never citable.
        assert "not" in _TOOL_BRIEFS["web_search"]
        assert "citable" in _TOOL_BRIEFS["web_search"]

    async def test_the_scene_is_the_shipped_workflow(self, scene: dict[str, Any]) -> None:
        assert scene["job"].workflow_version == WORKFLOW_VERSION


# ==========================================================================================
# The provider: the deterministic read of the vendor's response
# ==========================================================================================


def _search_response(
    *,
    stop_reason: str = "end_turn",
    content: list[Any] | None = None,
    searches: int = 1,
) -> Any:
    return SimpleNamespace(
        stop_reason=stop_reason,
        model="claude-haiku-4-5",
        content=content
        if content is not None
        else [
            SimpleNamespace(
                type="web_search_tool_result",
                content=[
                    SimpleNamespace(
                        type="web_search_result",
                        url="https://news.example.com/one",
                        title="Contoso announces results",
                        page_age="2 days ago",
                    ),
                    SimpleNamespace(
                        type="web_search_result",
                        url="https://news.example.com/two",
                        title="Contoso names a new CFO",
                        page_age="",
                    ),
                ],
            )
        ],
        usage=SimpleNamespace(
            input_tokens=100,
            output_tokens=20,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
            server_tool_use=SimpleNamespace(web_search_requests=searches),
        ),
    )


class _StubMessages:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = responses
        self.requests: list[dict[str, Any]] = []

    async def create(self, **request: Any) -> Any:
        self.requests.append(request)
        return self.responses[len(self.requests) - 1]


def _stub_provider(responses: list[Any]) -> tuple[AnthropicProvider, _StubMessages]:
    messages = _StubMessages(responses)
    client = SimpleNamespace(messages=messages)
    return AnthropicProvider(api_key="test", client=client), messages  # type: ignore[arg-type]


class TestTheProviderReadsTheListing:
    async def test_hits_and_the_billed_count_are_read_from_the_result_blocks(self) -> None:
        provider, messages = _stub_provider([_search_response()])

        outcome = await provider.search_web("contoso results", model="claude-haiku-4-5")

        assert [hit.title for hit in outcome.hits] == [
            "Contoso announces results",
            "Contoso names a new CFO",
        ]
        assert outcome.hits[0].page_age == "2 days ago"
        assert outcome.searches == 1
        # One search per call is the server's bound, not the prompt's.
        tool = messages.requests[0]["tools"][0]
        assert tool["max_uses"] == 1
        assert tool["name"] == "web_search"

    async def test_a_server_tool_error_raises_before_any_cost_exists(self) -> None:
        error_block = SimpleNamespace(
            type="web_search_tool_result",
            content=SimpleNamespace(error_code="query_too_long"),
        )
        provider, _ = _stub_provider([_search_response(content=[error_block], searches=0)])

        with pytest.raises(ExternalServiceError, match="query_too_long"):
            await provider.search_web("contoso", model="claude-haiku-4-5")

    async def test_pause_turn_is_resumed_and_the_bill_is_summed(self) -> None:
        paused = _search_response(stop_reason="pause_turn", content=[], searches=0)
        finished = _search_response()
        provider, messages = _stub_provider([paused, finished])

        outcome = await provider.search_web("contoso results", model="claude-haiku-4-5")

        assert len(messages.requests) == 2
        # The paused content went back as an assistant turn, unchanged.
        assert messages.requests[1]["messages"][1]["role"] == "assistant"
        assert outcome.usage.input_tokens == 200
        assert len(outcome.hits) == 2

    async def test_the_basic_tool_variant_is_used_for_models_that_need_it(self) -> None:
        provider, messages = _stub_provider([_search_response(), _search_response()])

        await provider.search_web("q", model="claude-haiku-4-5")
        await provider.search_web("q", model="claude-sonnet-5")

        assert messages.requests[0]["tools"][0]["type"] == "web_search_20250305"
        assert messages.requests[1]["tools"][0]["type"] == "web_search_20260209"


class TestThePrice:
    def test_the_verified_rate_is_ten_dollars_per_thousand(self) -> None:
        # ADR 0092's verification, held as arithmetic: $10 per 1,000 searches.
        assert Decimal("10") == WEB_SEARCH_USD_PER_CALL * 1000

    def test_the_fee_line_prices_searches_not_tokens(self) -> None:
        line = price_web_search(
            3, provider="anthropic", model="claude-haiku-4-5", usd_to_gbp=Decimal("0.8")
        )
        assert line is not None
        assert line.category.value == "web_search"
        assert line.unit_type == "searches"
        assert line.amount_usd == Decimal("0.03")
        assert line.amount_gbp == Decimal("0.024")

    def test_nothing_searched_is_no_line_at_all(self) -> None:
        assert (
            price_web_search(
                0, provider="anthropic", model="claude-haiku-4-5", usd_to_gbp=Decimal("0.8")
            )
            is None
        )
