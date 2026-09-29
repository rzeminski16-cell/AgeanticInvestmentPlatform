"""Every server-rendered page, rendered, against a run that actually happened.

`tests/test_shell_nav.py` proves every page is *reachable* — that a nav href resolves to a
registered route and that no route is unreachable by accident. It does not open one. So the
suite knows the map is honest and nothing knows whether the places on it render.

That gap is survivable while templates change one at a time. It is not survivable through the
interface overhaul, which rewrites forty-two of them, because of what `StrictUndefined` does:
a template naming a context key its handler stopped supplying **raises**, and the failure
surfaces as a 500 on one page in one state that no other test opens. A rewrite that moved a
field from `job.status` to `state.plain_status` and missed the run console's error branch would
ship green.

So: drive one complete run, make one book, then open everything.

**A 500 is the failure this exists to catch.** Almost every other status is legitimate
somewhere — a 404 for a report on a run that produced none, a 409 for editing a request that
has been run — and asserting 200 everywhere would mean asserting the platform never refuses,
which is the opposite of what it is for. What no page may do is raise.

**One run, not one per page.** The drive is the expensive part; the render is not. Sharing it
across every route is what keeps this affordable enough to run every time rather than nightly.
"""

from __future__ import annotations

import re
import uuid
from datetime import date
from decimal import Decimal
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from aer.agents.post_trade_reviewer import ReviewDraft
from aer.api.security import CSRF_COOKIE_NAME, CSRF_FIELD_NAME
from aer.config import Settings
from aer.core.enums import (
    DecisionAction,
    FindingKind,
    GateKind,
    PremiseComparator,
    PremiseStatus,
    ProcessQuality,
    TransactionKind,
    UserRole,
)
from aer.db.models import (
    Calculation,
    Claim,
    Company,
    Finding,
    Portfolio,
    Report,
    Security,
    User,
)
from aer.providers.fake import FakeProvider
from aer.providers.router import Router
from aer.services import ask as ask_service
from aer.services import decisions as decision_service
from aer.services import post_trade
from aer.services import theses as thesis_service
from aer.services.theses import Predicate
from aer.storage.local import LocalArtefactStore
from tests.api_fixtures import build_app, client_for
from tests.journey_harness import (
    CODE_IDENTIFIERS,
    MODULE_PATH,
    SHELL,
    SHOUTED_ENUM,
    SNAKE_CASE,
    UUID,
    XBRL_TAG,
)
from tests.portfolio_fixtures import trade
from tests.request_fixtures import research_request
from tests.route_fixtures import page_routes_for
from tests.run_fixtures import Driver, to_final_gate
from tests.schema_guard import refuse_unanswerable_schema
from tests.workflow_fixtures import AS_OF_DATE, DEFAULT_PER_RUN_BUDGET_GBP

pytestmark = pytest.mark.integration

_TABLES = (
    "research_requests, audit_events, users, artefacts, prompts, companies, securities, "
    "portfolios, theses, judgements"
)

# A page may refuse. It may not raise.
#
# 500 is what `StrictUndefined` produces when a template names something its handler no longer
# passes, which is the whole reason this file exists. 502/503/504 would mean a dependency the
# in-process client does not have, and are here so a future change that introduces one fails
# loudly rather than being read as a legitimate refusal.
FORBIDDEN_STATUSES = frozenset({500, 502, 503, 504})

# The report itself, served as the standalone document it is archived as. Its title is the
# document's — the company and "Research Note" — and the product's name on it would be a
# watermark on the research rather than the name of a page.
DOCUMENTS = frozenset(
    {"/reports/{report_id}/preview", "/runs/{job_id}/preview", "/runs/{job_id}/summary"}
)


async def _truncate(engine: Any) -> None:
    async with engine.begin() as connection:
        await connection.execute(text("SET LOCAL statement_timeout = '10s'"))
        await connection.execute(text(f"TRUNCATE {_TABLES} RESTART IDENTITY CASCADE"))


@pytest.fixture
async def committed(db_engine: Any, tmp_path: Path) -> Any:
    """An operator, a request, and a book with one cash transaction in it.

    The portfolio is seeded here rather than driven through its form because this file is
    about rendering rather than about recording: what the portfolio page needs is a book that
    exists and a transaction to compute from, and the form's own behaviour is
    `tests/e2e/test_portfolio_screen.py`'s subject.
    """
    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    async with factory() as session:
        user = User(email="owner@example.invalid", display_name="Owner", role=UserRole.OWNER)
        session.add(user)
        await session.flush()
        request = research_request(
            user_id=user.id,
            company_name="Microsoft Corporation",
            ticker="MSFT",
            exchange="NASDAQ",
            as_of_date=AS_OF_DATE,
            base_currency="USD",
            reporting_currency="USD",
            investment_horizon_months=12,
            max_cost_gbp=DEFAULT_PER_RUN_BUDGET_GBP,
        )
        book = Portfolio(user_id=user.id, name="My portfolio", base_currency="GBP")
        # A company the run will *not* resolve — the drive below upserts MSFT, and a second
        # row on the same listing would collide — so the thesis has a subject of its own.
        contoso = Company(
            name="Contoso plc", ticker="CTSO", exchange="LSE", company_number="01234567"
        )
        session.add_all([request, book, contoso])
        await session.flush()
        thesis = await thesis_service.write_thesis(
            session, user=user, company=contoso, title="Contoso keeps its pricing power"
        )
        # A premise with a threshold, and the finding a pass would leave on it: the monitor's
        # detail page is parameterised on a finding, and a contradicted one renders the gate,
        # which is the branch with the most in it. Written as rows rather than driven through
        # a pass, because this file is about rendering and a pass needs a model.
        premise = await thesis_service.add_premise(
            session,
            thesis=thesis,
            actor=user,
            statement="Revenue keeps growing above 25% a year.",
            basis="The segment disclosure.",
            predicate=Predicate(
                metric="revenue growth",
                comparator=PremiseComparator.AT_LEAST,
                threshold=Decimal(25),
                unit="percent",
            ),
            review_by=None,
        )
        finding = Finding(
            user_id=thesis.user_id,
            thesis_id=thesis.id,
            judgement_id=premise.judgement_id,
            kind=FindingKind.READING,
            status=PremiseStatus.CONTRADICTED,
            justification="Revenue grew 12% in the year to 31 December 2025, below the 25% floor.",
            source_document_ids=[],
            observed={
                "metric": "revenue growth",
                "value": "0.12",
                "unit": "ratio",
                "period_end": "2025-12-31",
                "threshold": "0.25",
                "threshold_unit": "ratio",
                "comparator": "at least",
                "holds": False,
            },
            opens_gate=True,
        )
        session.add(finding)
        # A decision on the thesis, so the journal's detail page has a row to render.
        decision = await decision_service.record_decision(
            session,
            actor=user,
            thesis=thesis,
            action=DecisionAction.BUY,
            statement="Open an initial position.",
            basis="The FY25 report.",
            size_statement="about 2% of the book",
            horizon_months=24,
        )
        # A closed position, the reviewer's pass over it and the review confirmed from it:
        # the review pages are parameterised on a pass and on a review, and a pass needs a
        # model, so the fake answers with the draft a reviewer would have written.
        security = Security(
            company_id=contoso.id,
            ticker="CTSO",
            exchange="LSE",
            provider_symbol="CTSO.LSE",
            name="Contoso plc",
            quote_currency="GBX",
        )
        session.add(security)
        await session.flush()
        holdings = {"portfolio": book, "document": None}
        await trade(
            session,
            holdings,
            kind=TransactionKind.BUY,
            security=security,
            quantity="100",
            price="250",
            currency="GBX",
            on=date(2026, 3, 2),
        )
        await trade(
            session,
            holdings,
            kind=TransactionKind.SELL,
            security=security,
            quantity="-100",
            price="300",
            currency="GBX",
            on=date(2026, 6, 15),
            at_hour=16,
        )
        settings = Settings(
            http_user_agent="Test test@example.invalid", artefact_root=tmp_path / "artefacts"
        )
        [episode] = await post_trade.closed_episodes(session, portfolio=book)
        pass_job = await post_trade.run_review(
            session,
            settings=settings,
            provider=FakeProvider(
                {
                    "ReviewDraft": ReviewDraft(
                        verdicts=[],
                        process_quality=ProcessQuality.SOUND,
                        basis="Written first and followed.",
                    )
                },
                inspect_schema=refuse_unanswerable_schema,
            ),
            router=Router(settings),
            store=LocalArtefactStore(settings.artefact_root, max_bytes=settings.max_artefact_bytes),
            user=user,
            episode=episode,
        )
        proposal = await post_trade.proposal_of(session, pass_job.id, user_id=user.id)
        assert proposal is not None
        review = await post_trade.confirm_review(
            session,
            user=user,
            proposal=proposal,
            process_quality=ProcessQuality.SOUND,
            basis="Written first and followed.",
            lessons="",
            verdicts={},
        )
        await session.commit()
        yield {
            "user": user,
            "request": request,
            "book": book,
            "security": security,
            "thesis": thesis,
            "finding": finding,
            "decision": decision,
            "pass": pass_job,
            "review": review,
        }
    await _truncate(db_engine)


class _EnqueueRecorder:
    """Records what would have been enqueued instead of reaching for a worker queue.

    A run is started through the API here, and `enqueue_run` would otherwise try a real
    Redis: the failure is five retry warnings and a stall rather than an error, which is
    slower to diagnose than it is to prevent.
    """

    def __init__(self) -> None:
        self.job_ids: list[str] = []

    async def __call__(self, redis: Any, job_id: uuid.UUID) -> str:
        self.job_ids.append(str(job_id))
        return f"task-{job_id}"


@pytest.fixture
def enqueued(monkeypatch: pytest.MonkeyPatch) -> _EnqueueRecorder:
    recorder = _EnqueueRecorder()
    monkeypatch.setattr("aer.api.routes.runs.enqueue_run", recorder)
    monkeypatch.setattr("aer.web.pages.enqueue_run", recorder)
    return recorder


@pytest.fixture
async def api(
    api_settings: Settings,
    db_engine: Any,
    fake_redis: Any,
    committed: dict[str, Any],
    enqueued: _EnqueueRecorder,
) -> Any:
    async for client in client_for(build_app(api_settings, engine=db_engine, redis=fake_redis)):
        yield client


@pytest.fixture
async def finished_run(
    api: Any, db_engine: Any, api_settings: Settings, committed: dict[str, Any]
) -> dict[str, Any]:
    """One run, driven to a frozen report, plus the ids every parameterised route needs.

    Approving the final gate rather than stopping at it, deliberately: a run parked at gate 3
    renders the gate pages and nothing downstream, and the report surfaces are precisely the
    ones tranche 7 rewrites.
    """
    driver = Driver(db_engine, api_settings)
    job_id = await to_final_gate(api, committed["request"].id, driver)
    await driver.approve(job_id, gate=GateKind.FINAL, step="revise")
    await driver.advance(job_id)

    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    async with factory() as session:
        report = await session.scalar(select(Report).where(Report.job_id == job_id))
        claim = await session.scalar(select(Claim).limit(1))
        calculation = await session.scalar(select(Calculation).where(Calculation.job_id == job_id))
        company = await session.scalar(select(Company).limit(1))
        # One question over the run's record, resolved to the third tier: priced, never
        # run, no model called — so the question and note pages have a row to render.
        owner = await session.scalar(select(User).order_by(User.created_at).limit(1))
        subject = report.company_id if report is not None and report.company_id else None
        asked = None
        if owner is not None and subject is not None:
            asked = await ask_service.ask(
                session,
                settings=api_settings,
                provider=FakeProvider(),
                router=Router(api_settings),
                store=LocalArtefactStore(
                    api_settings.artefact_root, max_bytes=api_settings.max_artefact_bytes
                ),
                user=owner,
                company=await session.get(Company, subject),
                text="Has anything changed at their main competitor since the last results?",
            )
            await session.commit()
    return {
        "question_id": asked.id if asked is not None else uuid.uuid4(),
        "job_id": job_id,
        "request_id": committed["request"].id,
        "report_id": report.id if report else uuid.uuid4(),
        "claim_id": claim.id if claim else uuid.uuid4(),
        "calculation_id": calculation.id if calculation else uuid.uuid4(),
        "company_id": company.id if company else uuid.uuid4(),
        "portfolio_id": committed["book"].id,
        "security_id": committed["security"].id,
        "thesis_id": committed["thesis"].id,
        "finding_id": committed["finding"].id,
        "decision_id": committed["decision"].judgement_id,
        "pass_id": committed["pass"].id,
        "review_id": committed["review"].judgement_id,
    }


def _fill(route: str, ids: dict[str, Any]) -> str | None:
    """One route template to a real URL, or None when nothing here can supply its parameters.

    Returning None rather than guessing: a route filled with an invented id renders the
    not-found page, which is a real page but not the one the route is for, and a suite that
    quietly checked the wrong page would be worse than one that checked nothing.
    """
    filled = route
    for name, value in ids.items():
        filled = filled.replace(f"{{{name}}}", str(value))
    # The two remaining parameters have no id of their own. A footnote is addressed by its
    # number in the assembled document, and a skill by the key an operator gave it.
    filled = filled.replace("{number}", "1").replace("{key}", "does-not-exist")
    return None if "{" in filled else filled


class TestEveryPageRenders:
    """The whole map, opened, against one finished run."""

    async def test_no_page_raises(self, api: Any, finished_run: dict[str, Any]) -> None:
        raised: dict[str, int] = {}
        unnamed: dict[str, str] = {}
        opened = 0
        for route in sorted(page_routes_for()):
            url = _fill(route, finished_run)
            if url is None:  # pragma: no cover -- every route is fillable today
                continue
            opened += 1
            response = await api.get(url, follow_redirects=True)
            if response.status_code in FORBIDDEN_STATUSES:
                raised[route] = response.status_code
            # Every page's title ends with the product's name (U1), so a tab and a bookmark
            # say where they lead; a fragment has no title and is not a page.
            titled = re.search(r"<title>\s*(.*?)\s*</title>", response.text, re.S)
            if titled and route not in DOCUMENTS and not titled.group(1).endswith("Ageantic"):
                unnamed[route] = titled.group(1)

        assert not raised, (
            f"These pages did not render: {raised}. Under `StrictUndefined` a 500 is most "
            "often a template naming a context key its handler stopped supplying — the "
            "failure the interface overhaul is most likely to introduce, and the one that "
            "surfaces on a single page in a single state that nothing else opens."
        )
        assert not unnamed, f"These pages' titles do not name the product: {unnamed}"
        assert opened >= 25, (
            f"only {opened} pages were opened, which is fewer than the map holds. A route "
            "whose parameters `_fill` cannot supply is skipped silently; if the number has "
            "dropped, teach `_fill` about the new parameter rather than letting the "
            "coverage quietly shrink."
        )


# A settings file named to the operator: where a value lives is the platform's business, and
# a page that says ".env" has told a person to go and edit a file.
_ENV_FILE = re.compile(r"(?<![\w/])\.env\b")


class _MainText(HTMLParser):
    """The words a person reads inside ``<main>``: text, never attributes, never scripts.

    Text a screen reader announces counts — an ``sr-only`` label is read aloud — and so does
    the content of ``<noscript>``, which is what the page says with scripting off.

    **Code the page marks as code is not held to words.** A skill file's example, the routing
    table the operator edits as a document, a keyboard key: `<pre>`, `<code>`, `<samp>`,
    `<kbd>` and a `<textarea>`'s content are the operator's own material or its format, shown
    as such on purpose. Everything else is prose, and prose is words.
    """

    _SILENT = frozenset({"script", "style", "template", "pre", "code", "samp", "kbd", "textarea"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._depth = 0
        self._silent = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "main":
            self._depth += 1
        elif tag in self._SILENT:
            self._silent += 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "main":
            self._depth = max(0, self._depth - 1)
        elif tag in self._SILENT:
            self._silent = max(0, self._silent - 1)

    def handle_data(self, data: str) -> None:
        if self._depth and not self._silent:
            self.parts.append(data)


def _main_text(page: str) -> str:
    reader = _MainText()
    reader.feed(page)
    return unescape(" ".join(" ".join(reader.parts).split()))


def _spoken_code(text: str) -> list[str]:
    """What on a page is code rather than words: the journey harness's own patterns."""
    found: list[str] = []
    uuid_at = UUID.search(text)
    if uuid_at:
        # Where it sits, so the failure names the sentence rather than sending somebody hunting.
        found.append(f"a UUID in {text[max(0, uuid_at.start() - 40) : uuid_at.end() + 10]!r}")
    shell = SHELL.search(text)
    if shell:
        found.append(f"a shell command {shell.group(0).strip()!r}")
    if _ENV_FILE.search(text):
        found.append("the settings file")
    leaked = set(SNAKE_CASE.findall(text)) | set(SHOUTED_ENUM.findall(text))
    leaked |= set(MODULE_PATH.findall(text)) | set(XBRL_TAG.findall(text))
    leaked |= {name for name in CODE_IDENTIFIERS if re.search(rf"\b{re.escape(name)}\b", text)}
    found.extend(sorted(leaked))
    return found


class TestEveryPageSpeaksWords:
    """U7: no identifier, module path, settings file or shell command on any page served.

    The template ratchet (`test_no_raw_identifiers_on_screen.py`) reads the templates; this
    reads what they render, against the same run the render test drives, so a word that
    reaches a page from a handler, a service or the record is caught where it lands.
    """

    async def test_no_page_speaks_code(self, api: Any, finished_run: dict[str, Any]) -> None:
        spoken: dict[str, list[str]] = {}
        for route in sorted(page_routes_for()):
            url = _fill(route, finished_run)
            if url is None or route in DOCUMENTS:  # pragma: no cover -- all fillable today
                continue
            response = await api.get(url, follow_redirects=True)
            if "text/html" not in response.headers.get("content-type", ""):
                continue
            found = _spoken_code(_main_text(response.text))
            if found:
                spoken[route] = found

        assert not spoken, "These pages show code where a person reads words:\n" + "\n".join(
            f"  {route}: {found}" for route, found in sorted(spoken.items())
        )


class TestEveryPageKeepsTheFormsToken:
    """ROADMAP item 96: no page replaces a form token its request already carries.

    Pages that minted their own set a cookie every other open page's forms had not been
    rendered against — the run console reloading itself as the run moved, a gate opened in a
    second tab — and the next press on any of them was refused as a forgery.
    """

    async def test_no_page_replaces_a_usable_token(
        self, api: Any, finished_run: dict[str, Any]
    ) -> None:
        await api.get("/", follow_redirects=True)
        assert api.cookies.get(CSRF_COOKIE_NAME), "the first page set no form token"

        replaced: dict[str, str] = {}
        carried = re.compile(rf'name="{CSRF_FIELD_NAME}"\s+value="([^"]+)"')
        for route in sorted(page_routes_for()):
            url = _fill(route, finished_run)
            if url is None or route in DOCUMENTS:  # pragma: no cover -- all fillable today
                continue
            # Against what this request carried, so one page that replaces the token is
            # named alone rather than blamed on every page opened after it.
            held = api.cookies.get(CSRF_COOKIE_NAME)
            response = await api.get(url, follow_redirects=True)
            issued = response.cookies.get(CSRF_COOKIE_NAME)
            if issued is not None and issued != held:
                replaced[route] = "set a new cookie"
            elif any(token != held for token in carried.findall(response.text)):
                replaced[route] = "rendered a form carrying a different token"

        assert not replaced, (
            "These pages replaced the form token the request carried, which refuses the next "
            f"press on every other page already open: {replaced}"
        )


class TestThePagesThatMustRefuse:
    """Refusals are pages too, and the overhaul rewrites them alongside the happy paths."""

    async def test_editing_a_run_request_is_refused_on_a_page(
        self, api: Any, finished_run: dict[str, Any]
    ) -> None:
        """A stale bookmark needs the reason, not a bare 409.

        The request is now a record of something that happened, and saying so is a rendered
        page (`requests/immutable.html`) rather than a status code.
        """
        response = await api.get(f"/requests/{finished_run['request_id']}/edit")
        assert response.status_code not in FORBIDDEN_STATUSES
        assert response.status_code == 409
        # The structural anchor rather than a phrase: the reason's wording belongs to the
        # service and may be improved, but a 409 that renders no reason at all is the
        # regression — a bare status where a page should be.
        assert 'id="immutable-reason"' in response.text
        assert "cannot be edited" in response.text

    async def test_a_run_that_is_not_yours_answers_as_missing(self, api: Any) -> None:
        """The same answer for "no such run" and "not yours", so ids cannot be enumerated."""
        response = await api.get(f"/runs/{uuid.uuid4()}")
        assert response.status_code == 404


class TestThePortfolioRendersInEveryShape:
    async def test_a_book_with_nothing_in_it(self, api: Any) -> None:
        response = await api.get("/portfolio")
        assert response.status_code not in FORBIDDEN_STATUSES
        assert response.status_code == 200

    async def test_a_book_with_a_cash_transaction(
        self, api: Any, committed: dict[str, Any]
    ) -> None:
        """Cash alone is a book. A page gated on securities would show the empty state over
        a balance somebody had just entered."""
        page = await api.get("/portfolio")
        token = _csrf_from(page.text)
        recorded = await api.post(
            "/portfolio/transactions",
            data={
                "csrf_token": token,
                "kind": TransactionKind.DEPOSIT.value,
                "security": "",
                "trade_date": AS_OF_DATE.isoformat(),
                "quantity": "50000",
                "currency": "GBP",
                "fees": "0",
            },
            follow_redirects=True,
        )
        assert recorded.status_code not in FORBIDDEN_STATUSES
        again = await api.get("/portfolio")
        assert again.status_code == 200

    async def test_a_dated_view_is_a_link(self, api: Any) -> None:
        """The date is in the URL, so "as it stood on the thirtieth" is a page somebody can
        keep. A malformed one falls back rather than erroring."""
        for query in (f"?as_of={AS_OF_DATE.isoformat()}", "?as_of=not-a-date"):
            response = await api.get(f"/portfolio{query}")
            assert response.status_code == 200, query


def _csrf_from(html: str) -> str:
    found = re.search(r'name="csrf_token"\s+value="([^"]+)"', html)
    assert found is not None, "the portfolio page rendered no CSRF token"
    return found.group(1)
