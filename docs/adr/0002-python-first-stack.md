# 2. Python-first stack, server-rendered GUI

- **Status:** Accepted
- **Date:** 2026-07-27

## Context

The platform needs a browser GUI on a local Windows machine, an asynchronous job engine
for runs that take 20–60 minutes, a relational store, a financial calculation engine, and
a document renderer producing institutional-quality PDFs.

Constraints that actually drive the choice:

- One developer, evenings and weekends, targeting a usable MVP in a few months.
- The priority order is **research quality first**, professional presentability second,
  learning value third.
- It must later be deployable as a hosted multi-user service without rewriting the core.
- The financial and data-handling ecosystem — XBRL parsing, PDF table extraction,
  numerical work — is overwhelmingly Python.

## Decision

**Python 3.12 throughout**, managed with `uv`.

- **FastAPI** for both the JSON API and server-rendered pages. Async-native, Pydantic-native,
  and it generates an OpenAPI document for free.
- **Jinja2 + HTMX + Tailwind** for the GUI. Server-rendered HTML, with HTMX providing live
  run progress over server-sent events and inline validation.
- **ARQ + Redis** for the job queue. Asyncio-native, so routes and jobs are both
  `async def` with no synchronous/asynchronous bridging.
- **PostgreSQL 16** with async SQLAlchemy 2.0 and Alembic.
- **WeasyPrint** for PDF, rendering the *same* HTML and CSS the web preview uses.

The typed REST API is kept clean and complete even though the pages are server-rendered,
so a React frontend can be added later without touching anything below the API layer.

## Consequences

- One language across the whole stack. No JavaScript build toolchain in CI; the Tailwind
  stylesheet is built once and committed.
- The web preview and the PDF share one template set, so what you approve is what you get
  — which matters because the approval gate is the product's core safety mechanism.
- HTMX will feel limiting for genuinely interactive components. Accepted: the GUI is
  forms, tables, progress and approval flows, which is exactly what server-rendering does
  well.
- WeasyPrint has native dependencies that can be awkward on Windows, and no JavaScript
  support. This is validated in Phase 0 rather than Phase 5, because it is the one tooling
  choice that could force a late rework.
- Python is slower than the alternatives for numerical work. Irrelevant at one report per
  week; correctness and traceability dominate.

## Alternatives considered

**Streamlit.** Days rather than weeks to a working GUI. Rejected: it looks like a
data-science demo, fights multi-step approval flows and live job progress, and would need
a full rewrite before commercialisation — undermining two of the project's three goals.

**FastAPI + Next.js/React.** The strongest employer-facing polish and the most "real
product" architecture. Rejected *for now*: it costs roughly three to five extra weeks and
splits attention between research quality and frontend work. The API contract is kept
clean specifically so this remains an easy later move.

**Celery, Prefect or Temporal** for orchestration. All heavier than a single-user local
application needs, and all want to own workflow state. This project needs step-level
state in Postgres regardless — for audit and reproducibility — so the queue only needs to
deliver work. ARQ does that with far less machinery.

**Typst or ReportLab** for PDF. Typst produces better typography but adds a non-Python
binary and a second template language. ReportLab means laying out an institutional report
imperatively. Both rejected in favour of sharing one HTML/CSS template set; Typst is
recorded as the upgrade path if the PDF ever looks insufficiently institutional.
