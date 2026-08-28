"""The FastAPI application factory.

``create_app()`` is a factory rather than a module-level ``app = FastAPI()`` for one
reason that matters and several that follow from it: importing a module must not open a
database connection pool. A module-level instance means every import — a test collecting,
a CLI printing its version, a migration loading models — drags the whole runtime with it.

Resources live on :class:`AppState`, built during the lifespan and reachable from any
handler through ``request.app.state.aer``. Tests may inject their own state; the lifespan
then leaves it alone, because whoever created a resource is responsible for closing it,
and a factory that disposes an engine it did not create will eventually dispose one still
in use.
"""

from __future__ import annotations

import mimetypes
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Final

import structlog
from fastapi import FastAPI
from redis.asyncio import Redis
from sqlalchemy.exc import SQLAlchemyError
from starlette.staticfiles import StaticFiles

from aer.api.errors import register_exception_handlers
from aer.api.middleware import RequestContextMiddleware
from aer.api.routes import (
    assumptions,
    calculations,
    claims,
    companies,
    health,
    knowledge,
    plans,
    reports,
    requests,
    runs,
    skills,
)
from aer.api.state import AppState
from aer.config import Settings, load_settings
from aer.db.engine import create_engine, create_session_factory
from aer.db.schema_check import schema_drift
from aer.logging import configure_logging
from aer.tracing import configure_tracing
from aer.version import build_identity, version
from aer.web import pages as web_pages
from aer.web import routes as web_routes
from aer.web import skills_pages
from aer.web.overview import pages as overview_pages
from aer.web.overview import research_pages as overview_research_pages
from aer.web.portfolio import pages as portfolio_pages
from aer.web.templating import STATIC_DIR
from aer.web.tools import pages as tool_pages

__all__ = ["AppState", "bootstrap", "create_app"]

_log = structlog.get_logger("aer.api.app")

_DESCRIPTION = """\
Local-first, auditable equity research for UK and US listed equities.

**Not investment advice.** Nothing this API returns is a recommendation to buy, sell or
hold any security.
"""


async def _warn_if_schema_is_behind(app_state: AppState) -> None:
    """Say so at start-up if the database is missing something the models expect.

    A warning, never a refusal. The landing page is deliberately built to render with the
    database down and say what is wrong, and an application that would not start because of
    a pending migration would take away the one page that could have told you. This is also
    why the check cannot fail start-up when it is the *database* that is unreachable: that
    is a state the application is required to survive.

    Logged rather than printed because ``aer serve`` streams these to the console anyway,
    and a print would bypass the redaction every other line goes through.
    """
    try:
        async with app_state.session_factory() as session:
            drift = await schema_drift(session)
    except (SQLAlchemyError, OSError) as exc:
        _log.warning("schema.check_skipped", reason=type(exc).__name__)
        return

    if drift.is_clean:
        return

    _log.warning(
        "schema.out_of_date",
        detail=drift.as_message(),
        missing_tables=list(drift.missing_tables),
        missing_columns=list(drift.missing_columns),
    )


def _build_state(settings: Settings) -> AppState:
    engine = create_engine(settings)
    return AppState(
        settings=settings,
        engine=engine,
        session_factory=create_session_factory(engine),
        # Decoding here rather than at each call site: every value this application puts
        # in Redis is text, and a bytes/str mismatch is a bug that only shows up on the
        # read path, far from the write that caused it.
        redis=Redis.from_url(settings.redis_url, decode_responses=True),
    )


def create_app(settings: Settings | None = None, *, state: AppState | None = None) -> FastAPI:
    """Build the application.

    Args:
        settings: Configuration to use. Loaded from the environment when omitted.
        state: Pre-built resources. When supplied they are used as-is and **not** closed
            on shutdown, so a test can share one engine across many requests.
    """
    resolved = settings or load_settings()
    injected = state is not None

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app_state = state if state is not None else _build_state(resolved)
        app.state.aer = app_state

        if not injected:
            resolved.ensure_directories()

        _log.info(
            "application.started",
            app_env=resolved.app_env,
            build=build_identity(),
            artefact_root=str(resolved.artefact_root),
        )
        await _warn_if_schema_is_behind(app_state)
        try:
            yield
        finally:
            if not injected:
                await app_state.engine.dispose()
                await app_state.redis.aclose()
            _log.info("application.stopped")

    app = FastAPI(
        title="Tracework Invest",
        description=_DESCRIPTION,
        version=version(),
        lifespan=lifespan,
        # The interactive docs are a development affordance, not a feature. In production
        # they advertise the whole surface to anyone who reaches the port.
        docs_url=None if resolved.is_production else "/docs",
        redoc_url=None,
        openapi_url=None if resolved.is_production else "/openapi.json",
    )

    app.add_middleware(RequestContextMiddleware)
    register_exception_handlers(app)

    app.include_router(health.router)
    app.include_router(requests.router)
    app.include_router(assumptions.router)
    app.include_router(calculations.router)
    app.include_router(claims.router)
    app.include_router(plans.router)
    app.include_router(runs.router)
    app.include_router(reports.router)
    app.include_router(companies.router)
    app.include_router(knowledge.router)
    app.include_router(skills.router)
    app.include_router(web_routes.router)
    app.include_router(web_pages.router)
    app.include_router(skills_pages.router)
    app.include_router(overview_pages.router)
    app.include_router(overview_research_pages.router)
    app.include_router(portfolio_pages.router)
    app.include_router(tool_pages.router)
    _register_local_media_types()
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    return app


# Media types Python does not know on its own, for files this application actually serves.
#
# `mimetypes` seeds itself from the host: `/etc/mime.types` on Linux, the registry on
# Windows. Only its small hardcoded table is the same everywhere, and `.woff2` is not in it
# — so the same font shipped from this repository leaves the server as `font/woff2` on one
# machine and `application/octet-stream` on another, decided by which operating system the
# operator happens to run.
#
# That is not cosmetic. `base.html` preloads the face as `<link rel="preload" as="font"
# type="font/woff2">`, and a preload whose declared type does not match the response is
# discarded and fetched again: the head start is paid for twice and the page is slower than
# with no preload at all. Nothing errors, which is why it survived until a Windows run of
# the acceptance sheet found it.
#
# `.woff` sits beside it deliberately. Nothing serves one today, and if anything ever does
# it fails exactly this silently. `.md` is here because `static/README.md` is inside the
# mounted directory and is therefore served, whether or not anybody meant it to be.
_LOCAL_MEDIA_TYPES: Final = {
    ".woff2": "font/woff2",
    ".woff": "font/woff",
    ".md": "text/markdown",
}


def _register_local_media_types() -> None:
    """Pin the types above, so what is served does not depend on the host's configuration."""
    for suffix, media_type in _LOCAL_MEDIA_TYPES.items():
        mimetypes.add_type(media_type, suffix)


def bootstrap() -> FastAPI:
    """Configure logging, then build the application.

    The entry point for ``uvicorn --factory aer.api.app:bootstrap``. Uvicorn's reloader
    re-imports the factory in a fresh subprocess, so anything that must happen before the
    first log line has to happen inside the factory rather than in the caller.
    """
    settings = load_settings()
    configure_logging(level=settings.log_level, json_output=settings.log_json)
    # Off unless AER_OTEL_ENDPOINT is set, and never a reason startup fails. See ADR 0049.
    configure_tracing(service_version=version())
    return create_app(settings)
