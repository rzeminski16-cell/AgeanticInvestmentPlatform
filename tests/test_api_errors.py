"""Error responses, request identity and what must never appear in a body.

The tests that matter here are negative ones. It is easy to write an error handler that
returns the right status; it is easy, and much more damaging, to write one that also
returns the exception's message, and the only thing standing between "we handle errors
consistently" and "we leak an internal path on every 500" is a test that looks.
"""

from __future__ import annotations

import logging

import pytest
from fastapi import APIRouter
from pydantic import BaseModel

from aer.api.errors import PROBLEM_MEDIA_TYPE
from aer.api.middleware import REQUEST_ID_HEADER
from aer.errors import (
    AerError,
    BudgetExceededError,
    ExternalServiceError,
    IntegrityError,
    ValidationError,
)
from tests.api_fixtures import build_app, client_for
from tests.log_helpers import events_at_or_above, structlog_events

SECRET_IN_A_MESSAGE = "postgresql://aer:hunter2@db.internal:5432/aer"  # pragma: allowlist secret


class Payload(BaseModel):
    ticker: str
    weight: float


def _probe_router() -> APIRouter:
    """Routes that fail in each of the ways the handlers are supposed to cover."""
    router = APIRouter(prefix="/_probe")

    @router.get("/boom")
    async def boom() -> None:
        raise RuntimeError(f"connection failed for {SECRET_IN_A_MESSAGE}")

    @router.get("/validation")
    async def domain_validation() -> None:
        raise ValidationError("as_of_date is in the future", context={"field": "as_of_date"})

    @router.get("/budget")
    async def budget() -> None:
        raise BudgetExceededError("Projected cost exceeds the per-run cap.")

    @router.get("/upstream")
    async def upstream() -> None:
        raise ExternalServiceError("EDGAR timed out", provider="sec_edgar", retryable=True)

    @router.get("/integrity")
    async def integrity() -> None:
        raise IntegrityError("Artefact hash mismatch", context={"sha256": "deadbeef"})

    @router.get("/leaky-context")
    async def leaky_context() -> None:
        # Not a real key. Its only job is to be a distinctive string we can assert never
        # reaches a response body.
        leaked = "sk-ant-api03-LEAKED"  # pragma: allowlist secret
        raise ValidationError("bad input", context={"api_key": leaked})

    @router.post("/schema")
    async def schema(payload: Payload) -> dict[str, str]:
        return {"ticker": payload.ticker}

    return router


@pytest.fixture
async def probe_client(api_settings, broken_engine, fake_redis):
    # The probes never touch a dependency, so an unreachable database keeps this file
    # runnable without PostgreSQL.
    app = build_app(api_settings, engine=broken_engine, redis=fake_redis)
    app.include_router(_probe_router())
    async for client in client_for(app):
        yield client


class TestUnexpectedExceptions:
    async def test_returns_500_with_a_request_id(self, probe_client):
        response = await probe_client.get("/_probe/boom")

        assert response.status_code == 500
        body = response.json()
        assert body["code"] == "internal_error"
        assert body["request_id"]
        assert body["request_id"] == response.headers[REQUEST_ID_HEADER]

    async def test_body_contains_no_traceback_and_no_internal_message(self, probe_client):
        raw = (await probe_client.get("/_probe/boom")).text

        assert "Traceback" not in raw
        assert "RuntimeError" not in raw
        assert SECRET_IN_A_MESSAGE not in raw
        assert "hunter2" not in raw

    async def test_the_traceback_is_logged_even_though_it_is_not_returned(
        self, probe_client, caplog, bridged_logging
    ):
        # The other half of the contract. Suppressing detail in the response is only
        # acceptable because it is recorded somewhere the operator can reach.
        with caplog.at_level(logging.ERROR, logger="aer.api.errors"):
            await probe_client.get("/_probe/boom")

        events = list(events_at_or_above(caplog.records, logging.ERROR))
        assert events
        logged = next(event for event in events if event["event"] == "unhandled_exception")
        assert "Traceback" in logged["exception"]
        assert "RuntimeError" in logged["exception"]

    async def test_the_response_is_problem_json(self, probe_client):
        response = await probe_client.get("/_probe/boom")
        assert response.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)


class TestAerErrorMapping:
    @pytest.mark.parametrize(
        ("path", "status", "code"),
        [
            ("/_probe/validation", 422, "validation_error"),
            ("/_probe/budget", 402, "budget_exceeded"),
            ("/_probe/upstream", 502, "external_service_error"),
            ("/_probe/integrity", 500, "integrity_error"),
        ],
    )
    async def test_status_and_code(self, probe_client, path, status, code):
        response = await probe_client.get(path)

        assert response.status_code == status
        body = response.json()
        assert body["code"] == code
        assert body["status"] == status
        assert body["type"] == f"/errors/{code}"

    async def test_a_deliberate_message_is_returned(self, probe_client):
        # Unlike an unexpected exception, an AerError message is written for a person to
        # act on. Hiding it would make the error useless.
        body = (await probe_client.get("/_probe/validation")).json()
        assert body["detail"] == "as_of_date is in the future"
        assert body["context"]["field"] == "as_of_date"

    async def test_context_is_redacted_on_the_way_out(self, probe_client):
        # Error context is not supposed to hold a credential. This is the last place one
        # could still be stopped.
        raw = (await probe_client.get("/_probe/leaky-context")).text
        assert "sk-ant-api03-LEAKED" not in raw

    async def test_every_error_class_has_a_distinct_code(self):
        subclasses = [AerError, ValidationError, BudgetExceededError, IntegrityError]
        codes = [cls.code for cls in subclasses]
        assert len(set(codes)) == len(codes)


class TestHttpExceptions:
    async def test_unknown_route_is_problem_json(self, probe_client):
        response = await probe_client.get("/no-such-page")

        assert response.status_code == 404
        assert response.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
        assert response.json()["code"] == "http_404"

    async def test_wrong_method_keeps_the_allow_header(self, probe_client):
        # The Allow header is what makes a 405 actionable. Rewriting the body must not
        # drop the headers that belong to the status.
        response = await probe_client.post("/healthz")

        assert response.status_code == 405
        assert "allow" in response.headers


class TestRequestValidation:
    async def test_schema_failure_is_422_with_the_field_named(self, probe_client):
        response = await probe_client.post("/_probe/schema", json={"ticker": "MSFT"})

        assert response.status_code == 422
        body = response.json()
        assert body["code"] == "request_validation_error"
        assert any("weight" in error["location"] for error in body["context"]["errors"])

    async def test_the_submitted_value_is_not_echoed_back(self, probe_client):
        # A form field is as likely to hold a mistyped credential as anything else.
        # Reflecting the input would put it in the response and in any log of one.
        response = await probe_client.post(
            "/_probe/schema",
            json={"ticker": "MSFT", "weight": "sk-ant-api03-TYPEDINTOTHEWRONGBOX"},
        )

        assert response.status_code == 422
        assert "TYPEDINTOTHEWRONGBOX" not in response.text


class TestRequestId:
    async def test_every_response_carries_one(self, probe_client):
        response = await probe_client.get("/healthz")
        assert response.headers[REQUEST_ID_HEADER]

    async def test_a_supplied_id_is_reused(self, probe_client):
        response = await probe_client.get("/healthz", headers={REQUEST_ID_HEADER: "abc-123"})
        assert response.headers[REQUEST_ID_HEADER] == "abc-123"

    @pytest.mark.parametrize(
        "hostile",
        [
            "a" * 200,
            "has spaces",
            "injected\x00null",
            "<script>alert(1)</script>",
        ],
    )
    async def test_a_hostile_id_is_replaced_not_echoed(self, probe_client, hostile):
        # The id is written into every log line for the request and returned in a header.
        # Accepting arbitrary text would let a caller forge log entries or bloat them.
        response = await probe_client.get("/healthz", headers={REQUEST_ID_HEADER: hostile})

        returned = response.headers[REQUEST_ID_HEADER]
        assert returned != hostile
        assert len(returned) == 32

    async def test_ids_differ_between_requests(self, probe_client):
        first = (await probe_client.get("/healthz")).headers[REQUEST_ID_HEADER]
        second = (await probe_client.get("/healthz")).headers[REQUEST_ID_HEADER]
        assert first != second

    async def test_the_id_reaches_the_logs(self, probe_client, caplog, bridged_logging):
        # The point of the id: given one from a response or an error body, every log line
        # for that request can be found.
        with caplog.at_level(logging.INFO):
            await probe_client.get("/_probe/validation", headers={REQUEST_ID_HEADER: "trace-me"})

        events = list(structlog_events(caplog.records))
        assert events
        # Every line, not merely one: the id is only useful for tracing if the whole
        # request is joinable by it, error line and access line alike.
        assert all(event["request_id"] == "trace-me" for event in events)
        assert {"aer_error", "request.completed"} <= {event["event"] for event in events}
