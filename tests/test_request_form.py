"""The server-rendered request form.

Three things are being protected here, in descending order of how expensive they would be
to get wrong:

1. **CSRF.** This application runs on loopback with no authentication, so any page in any
   tab can POST to it. A missing token check means a page the operator merely visited can
   commission spending.
2. **The no-JavaScript path.** The plain POST is the real one. A form whose validation
   only happens in the browser accepts anything the moment the script fails to load.
3. **Not losing the operator's input.** A rejected submission that discards a page of
   carefully written focus questions is a form people stop using.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from aer.api.security import CSRF_COOKIE_NAME, issue_csrf_token
from aer.core.enums import JobStatus, UserRole
from aer.db.models import Cost, Job, JobCancellation, Report, ResearchRequest, User
from aer.errors import ValidationError
from aer.runtime import Registers
from aer.services import runs as run_service
from tests.api_fixtures import build_app, client_for

pytestmark = pytest.mark.integration

NEW = "/requests/new"
_TOKEN = re.compile(r'name="csrf_token" value="([^"]+)"')


def valid_form(**overrides) -> dict[str, str]:
    form = {
        "company_name": "Microsoft Corporation",
        "ticker": "msft",
        "exchange": "NASDAQ",
        "isin": "",
        "base_currency": "USD",
        "reporting_currency": "",
        "investment_horizon_months": "36",
        "horizon_label": "Through the next capex cycle",
        "analysis_mode": "full",
        "undated_sources_admissible": "true",
        "current_weight_percent": "2.5",
        "maximum_weight_percent": "5",
        "benchmark": "MSCI World",
        "risk_tolerance": "balanced",
        "liquidity_constraint_gbp": "",
        "esg_sensitivity": "considered",
        "focus_questions": "How durable is the Azure margin?\nWhat breaks the bull case?",
        "excluded_sources": "seekingalpha.com",
        "max_cost_gbp": "2.00",
    }
    form.update(overrides)
    return form


@pytest.fixture
async def web(api_settings, db_engine, fake_redis):
    async with db_engine.begin() as connection:
        await connection.execute(text("SET LOCAL statement_timeout = '5s'"))
        await connection.execute(
            text("TRUNCATE research_requests, audit_events, users RESTART IDENTITY CASCADE")
        )
    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    async with factory() as session:
        session.add(User(email="form@example.invalid", display_name="Form", role=UserRole.OWNER))
        await session.commit()

    async for client in client_for(build_app(api_settings, engine=db_engine, redis=fake_redis)):
        yield client


async def fresh_token(client) -> str:
    page = await client.get(NEW)
    match = _TOKEN.search(page.text)
    assert match, "the form must render a CSRF token"
    return match.group(1)


async def count_requests(engine) -> int:
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as session:
        return len((await session.scalars(select(ResearchRequest))).all())


class TestFormRenders:
    async def test_the_page_loads(self, web):
        response = await web.get(NEW)

        assert response.status_code == 200
        assert "Commission a report" in response.text

    async def test_it_issues_a_csrf_cookie_and_a_matching_hidden_input(self, web):
        response = await web.get(NEW)

        cookie = response.cookies.get(CSRF_COOKIE_NAME)
        assert cookie
        assert _TOKEN.search(response.text).group(1) == cookie

    async def test_the_cookie_is_samesite_strict(self, web):
        # Lax would still send the cookie on a top-level cross-site GET. There is no
        # cross-site navigation into this application worth supporting.
        header = (await web.get(NEW)).headers["set-cookie"]
        assert "samesite=strict" in header.lower()

    async def test_the_form_states_the_date_rather_than_asking_for_it(self, web):
        # ADR 0110. There is no input, because there is no choice: the run is dated the
        # day it is commissioned. Stating it is not decoration — the report and every
        # price the run reads carry that date, and the operator should see it first.
        page = (await web.get(NEW)).text
        today = datetime.now(UTC).date().isoformat()

        assert 'name="as_of_date"' not in page
        assert 'id="as-of-statement"' in page
        assert today in page

    async def test_only_supported_exchanges_are_offered(self, web):
        body = (await web.get(NEW)).text
        assert 'value="NASDAQ"' in body
        assert 'value="LSE"' in body
        assert 'value="OTCQB"' not in body
        assert 'value="TSX"' not in body


class TestCsrf:
    async def test_a_submission_without_a_token_is_refused(self, web, db_engine):
        response = await web.post(NEW, data=valid_form())

        assert response.status_code == 403
        assert await count_requests(db_engine) == 0

    async def test_a_forged_token_is_refused(self, web, db_engine):
        response = await web.post(NEW, data=valid_form(csrf_token="forged.9999999999.deadbeef"))

        assert response.status_code == 403
        assert await count_requests(db_engine) == 0

    async def test_a_token_from_a_different_key_is_refused(self, web, db_engine):
        # The property that makes this a *signed* double submit: setting the cookie is not
        # enough, because the value has to carry this server's signature.
        foreign = issue_csrf_token(b"an-entirely-different-signing-key")
        web.cookies.set(CSRF_COOKIE_NAME, foreign)
        response = await web.post(NEW, data=valid_form(csrf_token=foreign))

        assert response.status_code == 403
        assert await count_requests(db_engine) == 0

    async def test_a_valid_token_in_the_body_but_not_the_cookie_is_refused(self, web, db_engine):
        # Half a double submit is not a double submit. A cross-origin page can cause the
        # cookie to be sent but cannot read it, which is exactly what this asserts.
        token = await fresh_token(web)
        web.cookies.delete(CSRF_COOKIE_NAME)
        response = await web.post(NEW, data=valid_form(csrf_token=token))

        assert response.status_code == 403
        assert await count_requests(db_engine) == 0

    async def test_a_refusal_hands_the_input_back(self, web):
        response = await web.post(NEW, data=valid_form())

        assert "security token" in response.text
        assert "Microsoft Corporation" in response.text
        assert "How durable is the Azure margin?" in response.text

    async def test_a_refusal_issues_a_usable_new_token(self, web, db_engine):
        # The old token may be exactly what failed; handing back a form carrying it would
        # guarantee a second failure.
        refused = await web.post(NEW, data=valid_form())
        retry_token = _TOKEN.search(refused.text).group(1)

        accepted = await web.post(NEW, data=valid_form(csrf_token=retry_token))

        assert accepted.status_code == 303
        assert await count_requests(db_engine) == 1


class TestSuccessfulSubmission:
    async def test_it_redirects_with_see_other(self, web):
        token = await fresh_token(web)
        response = await web.post(NEW, data=valid_form(csrf_token=token))

        # 303, not 302: it forces the follow-up to be a GET, so refreshing the detail page
        # cannot resubmit the form.
        assert response.status_code == 303
        assert response.headers["location"].startswith("/requests/")

    async def test_the_detail_page_shows_what_was_submitted(self, web):
        token = await fresh_token(web)
        created = await web.post(NEW, data=valid_form(csrf_token=token))
        detail = await web.get(created.headers["location"])

        assert detail.status_code == 200
        assert "Microsoft Corporation" in detail.text
        assert ">MSFT<" in detail.text
        assert "How durable is the Azure margin?" in detail.text
        assert "seekingalpha.com" in detail.text

    async def test_percentages_are_stored_as_fractions_and_shown_as_percentages(self, web):
        token = await fresh_token(web)
        created = await web.post(NEW, data=valid_form(csrf_token=token))
        detail = await web.get(created.headers["location"])

        assert "2.5%" in detail.text
        assert "5%" in detail.text

    async def test_the_stored_weight_is_a_fraction(self, web, db_engine):
        token = await fresh_token(web)
        await web.post(NEW, data=valid_form(csrf_token=token))

        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as session:
            row = await session.scalar(select(ResearchRequest))
        assert row.portfolio_context["current_weight"] == "0.025"

    async def test_refusing_undated_sources_is_stored_as_false(self, web, db_engine):
        """The control is a pair of radios, so "refuse" arrives as the word `false`.

        The parser once read ``values[name] != ""``, which was right for the checkbox the
        field was first built as and has been wrong since it became radios: `"false"` is
        not empty, so an operator who chose the second option got the first and nothing
        said otherwise. The policy is its own since ADR 0111 and the only one since ADR
        0113.
        """
        token = await fresh_token(web)
        await web.post(NEW, data=valid_form(csrf_token=token, undated_sources_admissible="false"))

        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as session:
            row = await session.scalar(select(ResearchRequest))
        assert row.work_order.undated_sources_admissible is False

    async def test_a_missing_decision_keeps_the_default(self, web, db_engine):
        """Nothing selected is not a decision.

        A radio group always submits something, so this is the malformed submission rather
        than the ordinary one — and the safe reading is the platform's default.
        """
        token = await fresh_token(web)
        form = valid_form(csrf_token=token)
        del form["undated_sources_admissible"]
        await web.post(NEW, data=form)

        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as session:
            row = await session.scalar(select(ResearchRequest))
        assert row.work_order.undated_sources_admissible is True

    async def test_the_new_request_appears_in_the_list(self, web):
        token = await fresh_token(web)
        await web.post(NEW, data=valid_form(csrf_token=token))

        listing = await web.get("/requests")
        assert "Microsoft Corporation" in listing.text


class TestRejectedSubmission:
    async def test_a_submitted_as_of_date_changes_nothing(self, web, db_engine):
        """ADR 0110. The field is gone from the form, so a posted one is not read.

        Not a 422: `parse_request_form` builds the payload from the fields the form
        renders, and a stray key in the body is a key nobody asked for. What matters is
        that it cannot become the run's date — the stamp is the clock, whatever arrives.
        """
        tomorrow = (datetime.now(UTC).date() + timedelta(days=1)).isoformat()
        token = await fresh_token(web)

        response = await web.post(NEW, data=valid_form(csrf_token=token, as_of_date=tomorrow))

        assert response.status_code == 303
        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as session:
            row = await session.scalar(select(ResearchRequest))
        assert row.work_order.as_of_date == datetime.now(UTC).date()

    async def test_an_etf_is_rejected_with_the_reason(self, web, db_engine):
        token = await fresh_token(web)
        response = await web.post(
            NEW,
            data=valid_form(
                csrf_token=token,
                ticker="SPY",
                company_name="SPDR S&P 500 ETF Trust",
                exchange="NYSE",
            ),
        )

        assert response.status_code == 422
        assert "fund rather than an operating company" in response.text
        assert await count_requests(db_engine) == 0

    async def test_a_malformed_ticker_is_rejected(self, web, db_engine):
        token = await fresh_token(web)
        response = await web.post(NEW, data=valid_form(csrf_token=token, ticker="NOT A TICKER"))

        assert response.status_code == 422
        assert await count_requests(db_engine) == 0

    async def test_a_non_numeric_weight_says_so_rather_than_disappearing(self, web):
        # A typo turning into "unspecified" is how a weight quietly vanishes from a
        # request nobody notices is wrong.
        token = await fresh_token(web)
        response = await web.post(
            NEW, data=valid_form(csrf_token=token, current_weight_percent="two point five")
        )

        assert response.status_code == 422
        assert "must be a number" in response.text

    async def test_every_answer_is_handed_back(self, web):
        token = await fresh_token(web)
        response = await web.post(NEW, data=valid_form(csrf_token=token, ticker="NOT A TICKER"))

        assert "Microsoft Corporation" in response.text
        assert "How durable is the Azure margin?" in response.text
        assert "MSCI World" in response.text
        assert 'value="36"' in response.text

    async def test_the_error_summary_links_to_the_offending_field(self, web):
        token = await fresh_token(web)
        response = await web.post(NEW, data=valid_form(csrf_token=token, ticker="NOT A TICKER"))

        assert 'href="#ticker"' in response.text

    async def test_the_submitted_value_is_not_echoed_into_an_error_message(self, web):
        # Form fields collect whatever gets pasted into them. Reflecting the value into the
        # error text would put a mistyped credential on the page and into any log of it.
        token = await fresh_token(web)
        response = await web.post(
            NEW, data=valid_form(csrf_token=token, base_currency="sk-ant-api03-WRONGBOX")
        )

        assert "WRONGBOX" not in response.text


class TestHtmxEnhancement:
    async def test_an_htmx_failure_returns_only_the_error_fragment(self, web):
        token = await fresh_token(web)
        response = await web.post(
            NEW,
            data=valid_form(csrf_token=token, ticker="NOT A TICKER"),
            headers={"HX-Request": "true"},
        )

        assert response.status_code == 422
        assert "<!doctype" not in response.text.lower()
        assert "This request was not created" in response.text

    async def test_an_htmx_success_asks_the_browser_to_navigate(self, web):
        # A 303 would be followed by HTMX and the whole detail page swapped into the error
        # container. HX-Redirect makes it a navigation instead.
        token = await fresh_token(web)
        response = await web.post(
            NEW, data=valid_form(csrf_token=token), headers={"HX-Request": "true"}
        )

        assert response.status_code == 204
        assert response.headers["hx-redirect"].startswith("/requests/")

    async def test_both_paths_enforce_the_same_rules(self, web, db_engine):
        # The point of the whole arrangement: HTMX changes where the answer is rendered,
        # never what the answer is.
        etf = {"ticker": "SPY", "company_name": "SPDR S&P 500 ETF Trust", "exchange": "NYSE"}

        plain = await web.post(NEW, data=valid_form(csrf_token=await fresh_token(web), **etf))
        htmx = await web.post(
            NEW,
            data=valid_form(csrf_token=await fresh_token(web), **etf),
            headers={"HX-Request": "true"},
        )

        assert plain.status_code == htmx.status_code == 422
        assert await count_requests(db_engine) == 0

    async def test_the_error_fragment_refreshes_the_csrf_token_out_of_band(self, web):
        # The bug this guards against is invisible over HTTP and fatal in a browser. HTMX
        # swaps only the error container, so the form's hidden input keeps whatever token
        # it was rendered with. Rotate the cookie without rotating that input and the two
        # disagree from then on: the form looks normal and every further submission is
        # refused. The out-of-band swap is what keeps them together.
        token = await fresh_token(web)
        response = await web.post(
            NEW,
            data=valid_form(csrf_token=token, ticker="NOT A TICKER"),
            headers={"HX-Request": "true"},
        )

        assert 'hx-swap-oob="true"' in response.text
        oob = re.search(r'id="csrf-input"[^>]*value="([^"]+)"', response.text)
        assert oob, "the error fragment must carry a replacement CSRF input"
        assert oob.group(1) == response.cookies.get(CSRF_COOKIE_NAME)

    async def test_a_full_page_render_does_not_duplicate_the_csrf_input(self, web):
        # The other half: in a full-page render the whole form is rebuilt, so emitting the
        # out-of-band copy too would put two inputs with the same id and the same name on
        # the page.
        token = await fresh_token(web)
        response = await web.post(NEW, data=valid_form(csrf_token=token, ticker="NOT A TICKER"))

        assert response.text.count('id="csrf-input"') == 1
        assert "hx-swap-oob" not in response.text

    async def test_correcting_an_htmx_rejection_and_resubmitting_succeeds(self, web, db_engine):
        token = await fresh_token(web)
        rejected = await web.post(
            NEW,
            data=valid_form(csrf_token=token, ticker="NOT A TICKER"),
            headers={"HX-Request": "true"},
        )
        refreshed = re.search(r'id="csrf-input"[^>]*value="([^"]+)"', rejected.text).group(1)

        accepted = await web.post(
            NEW,
            data=valid_form(csrf_token=refreshed),
            headers={"HX-Request": "true"},
        )

        assert accepted.status_code == 204
        assert await count_requests(db_engine) == 1

    async def test_the_form_posts_normally_without_javascript(self, web):
        # The plain action is the real path; hx-post only layers on top of it. Without
        # both, a browser with JavaScript disabled would have nowhere to submit.
        body = (await web.get(NEW)).text
        assert 'action="/requests/new"' in body
        assert 'method="post"' in body


class TestDetailPage:
    async def test_an_unknown_id_renders_a_404_page(self, web):
        response = await web.get("/requests/00000000-0000-0000-0000-000000000000")

        assert response.status_code == 404
        assert "not found" in response.text.lower()

    async def test_it_says_nothing_has_been_spent(self, web):
        token = await fresh_token(web)
        created = await web.post(NEW, data=valid_form(csrf_token=token))
        detail = await web.get(created.headers["location"])

        assert "nothing has been spent" in detail.text.lower()

    async def test_it_says_the_ticker_is_unconfirmed(self, web):
        token = await fresh_token(web)
        created = await web.post(NEW, data=valid_form(csrf_token=token))
        detail = await web.get(created.headers["location"])

        assert "not yet confirmed" in detail.text

    async def test_it_carries_the_disclaimer(self, web):
        token = await fresh_token(web)
        created = await web.post(NEW, data=valid_form(csrf_token=token))
        detail = await web.get(created.headers["location"])

        assert "not regulated investment advice" in detail.text


async def create_draft(client) -> str:
    """A saved draft, returned as its detail path."""
    token = await fresh_token(client)
    created = await client.post(NEW, data=valid_form(csrf_token=token))
    assert created.status_code == 303, created.text
    return created.headers["location"]


async def token_from(client, path: str) -> str:
    page = await client.get(path)
    match = _TOKEN.search(page.text)
    assert match, f"no CSRF token on {path}"
    return match.group(1)


async def give_it_a_run(engine, detail_path: str) -> None:
    """Start a run for the request at ``detail_path``, as pressing the button would."""
    request_id = uuid.UUID(detail_path.rsplit("/", 1)[-1])
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as session:
        request = await session.get(ResearchRequest, request_id)
        assert request is not None
        await run_service.start_run(session, request=request)
        await session.commit()


class TestTheEditForm:
    async def test_the_detail_page_offers_to_edit_a_draft(self, web):
        detail = await create_draft(web)
        assert 'id="edit-request"' in (await web.get(detail)).text

    async def test_the_form_is_prefilled_with_what_was_saved(self, web):
        detail = await create_draft(web)

        page = await web.get(f"{detail}/edit")

        assert page.status_code == 200
        assert 'value="MSFT"' in page.text
        assert 'value="Microsoft Corporation"' in page.text
        # The percentage the operator typed, not the fraction that was stored. A form that
        # renders 0.025 into a box labelled "%" silently divides the weight by a hundred
        # every time it is saved.
        assert 'value="2.5"' in page.text

    async def test_it_posts_back_to_itself(self, web):
        detail = await create_draft(web)
        page = await web.get(f"{detail}/edit")

        # Not to /requests/new. A rejected edit re-rendered as the create form would make
        # the next submission create a second request instead of fixing the first.
        assert f'action="{detail}/edit"' in page.text

    async def test_saving_a_change_updates_the_request(self, web):
        detail = await create_draft(web)
        token = await token_from(web, f"{detail}/edit")

        response = await web.post(
            f"{detail}/edit",
            data=valid_form(csrf_token=token, company_name="Microsoft Corp."),
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert response.headers["location"] == detail
        assert "Microsoft Corp." in (await web.get(detail)).text

    async def test_a_rejected_edit_keeps_the_operators_input(self, web, db_engine):
        detail = await create_draft(web)
        token = await token_from(web, f"{detail}/edit")

        response = await web.post(
            f"{detail}/edit",
            data=valid_form(csrf_token=token, exchange="TSX", horizon_label="Kept, please"),
        )

        assert response.status_code == 422
        assert "Kept, please" in response.text
        # "not saved", not "not created". The operator is editing something that exists.
        assert "not saved" in response.text
        assert "Kept, please" not in (await web.get(detail)).text

    async def test_a_submission_without_a_csrf_token_changes_nothing(self, web, db_engine):
        detail = await create_draft(web)

        response = await web.post(f"{detail}/edit", data=valid_form(company_name="Hijacked Ltd"))

        assert response.status_code == 403
        assert "Hijacked Ltd" not in (await web.get(detail)).text

    async def test_the_form_is_refused_once_a_run_exists(self, web, db_engine):
        detail = await create_draft(web)
        await give_it_a_run(db_engine, detail)

        page = await web.get(f"{detail}/edit")

        assert page.status_code == 409
        # The reason, not a bare status. The operator almost certainly followed a stale tab.
        # A queued run is live, so the answer is "wait or cancel", not "create a new
        # request" — the run has not left anything behind yet.
        assert "cancel it" in page.text
        assert 'id="immutable-reason"' in page.text

    async def test_the_detail_page_explains_why_editing_stopped(self, web, db_engine):
        detail = await create_draft(web)
        await give_it_a_run(db_engine, detail)

        page = await web.get(detail)

        assert 'id="edit-request"' not in page.text
        assert 'id="immutable-reason"' in page.text

    async def test_saving_after_a_run_started_is_refused(self, web, db_engine):
        # The race the guard exists for: the form was loaded while the request was a draft
        # and submitted after a run began.
        detail = await create_draft(web)
        token = await token_from(web, f"{detail}/edit")
        await give_it_a_run(db_engine, detail)

        response = await web.post(
            f"{detail}/edit", data=valid_form(csrf_token=token, ticker="AAPL")
        )

        assert response.status_code == 409
        assert "AAPL" not in (await web.get(detail)).text


class TestDeletingADraft:
    async def test_the_detail_page_offers_to_delete_a_draft(self, web):
        detail = await create_draft(web)
        assert 'id="delete-request"' in (await web.get(detail)).text

    async def test_deleting_removes_it_and_returns_to_the_list(self, web, db_engine):
        detail = await create_draft(web)
        token = await token_from(web, detail)

        response = await web.post(
            f"{detail}/delete", data={"csrf_token": token}, follow_redirects=False
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/requests"
        assert (await web.get(detail)).status_code == 404
        assert await count_requests(db_engine) == 0

    async def test_a_delete_without_a_csrf_token_deletes_nothing(self, web, db_engine):
        detail = await create_draft(web)

        response = await web.post(f"{detail}/delete")

        assert response.status_code == 403
        assert await count_requests(db_engine) == 1

    async def test_a_get_cannot_delete(self, web, db_engine):
        # A destructive GET is reachable by a prefetch, a link checker or an image tag.
        detail = await create_draft(web)

        assert (await web.get(f"{detail}/delete")).status_code == 405
        assert await count_requests(db_engine) == 1

    async def test_deleting_is_refused_once_a_run_exists(self, web, db_engine):
        detail = await create_draft(web)
        token = await token_from(web, detail)
        await give_it_a_run(db_engine, detail)

        response = await web.post(f"{detail}/delete", data={"csrf_token": token})

        assert response.status_code == 409
        assert await count_requests(db_engine) == 1

    async def test_the_button_is_gone_once_a_run_exists(self, web, db_engine):
        detail = await create_draft(web)
        await give_it_a_run(db_engine, detail)

        assert 'id="delete-request"' not in (await web.get(detail)).text


class TestTheLandingPageWarnsAboutAPendingMigration:
    """The page an operator opens when something is wrong should say what is wrong.

    A schema one migration behind can leave this page working perfectly — it touches none
    of the new tables — while the run console returns an opaque 500. Checking eagerly is
    what makes this the page that tells you.
    """

    @pytest.fixture
    async def missing_a_table(self, db_engine):
        async with db_engine.begin() as connection:
            await connection.execute(text("DROP TABLE IF EXISTS job_cancellations CASCADE"))
        try:
            yield
        finally:
            async with db_engine.begin() as connection:
                await connection.run_sync(JobCancellation.__table__.create, checkfirst=True)

    async def test_it_still_renders(self, web, missing_a_table):
        # Degraded, not broken. An application that refused to serve this page would take
        # away the only thing that could have explained the failure.
        assert (await web.get("/")).status_code == 200

    async def test_it_names_the_missing_table_and_the_command(self, web, missing_a_table):
        body = (await web.get("/")).text

        assert 'id="startup-problem"' in body
        assert "job_cancellations" in body
        assert "alembic upgrade head" in body

    async def test_a_migrated_database_shows_no_such_banner(self, web):
        assert 'id="startup-problem"' not in (await web.get("/")).text


# -- Archiving and removing, through the form surface -----------------------------------------


LIST = "/requests"
_LIST_TOKEN = re.compile(r'name="csrf_token" value="([^"]+)"')


async def a_saved_request(client) -> str:
    """Create one through the form and return its id."""
    token = await fresh_token(client)
    created = await client.post(NEW, data=valid_form(csrf_token=token))
    assert created.status_code == 303
    return created.headers["location"].rsplit("/", 1)[-1]


async def list_token(client, url: str = LIST) -> str:
    """The token the list page renders into its per-row forms.

    Takes the URL because the archive view is a different page with its own token, and
    restoring is done from there — an empty live list renders no rows and so no token,
    which is correct: there is nothing on it to protect.
    """
    page = await client.get(url)
    match = _LIST_TOKEN.search(page.text)
    assert match, f"{url} must issue a CSRF token for its per-row actions"
    return match.group(1)


class TestTheListPageIssuesATokenForItsActions:
    async def test_it_renders_one(self, web):
        await a_saved_request(web)

        assert _LIST_TOKEN.search((await web.get(LIST)).text) is not None

    async def test_the_row_offers_both_actions(self, web):
        request_id = await a_saved_request(web)

        body = (await web.get(LIST)).text

        assert f'action="/requests/{request_id}/archive"' in body
        assert f'href="/requests/{request_id}/remove"' in body


class TestTheListSaysWhatEachRequestBecame:
    """A row's state is its newest run's, and its spend is what its runs were billed.

    Nothing in the research workflow moves a request past *Draft*, and no research run writes
    the job's running total, so the list said *Draft* and £0.00 beside every report the
    operator had approved (ROADMAP §3.19 item 87). It reads the run and the cost rows now,
    which are what the console and the costs page read.
    """

    async def test_a_request_that_ran_shows_its_run_and_what_it_cost(self, web, db_engine):
        request_id = uuid.UUID(await a_saved_request(web))
        await _a_run(db_engine, request_id, JobStatus.SUCCEEDED, costs=("1.20", "0.17"))

        row = _row_of((await web.get(LIST)).text, request_id)

        assert "Finished" in row
        assert "Draft" not in row
        assert "£1.37" in row

    async def test_the_newest_run_is_the_one_shown(self, web, db_engine):
        request_id = uuid.UUID(await a_saved_request(web))
        await _a_run(db_engine, request_id, JobStatus.CANCELLED, costs=("0.50",), hours_ago=2)
        await _a_run(db_engine, request_id, JobStatus.AWAITING_APPROVAL, costs=("0.25",))

        row = _row_of((await web.get(LIST)).text, request_id)

        assert "Waiting for you" in row
        assert "Cancelled" not in row
        # Every run the request has had, not only the newest one's.
        assert "£0.75" in row

    async def test_a_request_that_never_ran_is_still_a_draft_that_cost_nothing(self, web):
        request_id = uuid.UUID(await a_saved_request(web))

        row = _row_of((await web.get(LIST)).text, request_id)

        assert "Draft" in row
        assert "£0.00" in row


async def _a_run(
    db_engine,
    request_id: uuid.UUID,
    status: JobStatus,
    *,
    costs: tuple[str, ...],
    hours_ago: int = 0,
) -> None:
    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    async with factory() as session:
        job = Job(
            work_order_id=request_id,
            workflow_version="test",
            code_version="test",
            status=status,
            started_at=datetime.now(UTC) - timedelta(hours=hours_ago),
        )
        session.add(job)
        await session.flush()
        session.add_all(
            Cost(
                job_id=job.id,
                category="llm_output",
                provider="anthropic",
                units=Decimal(1000),
                unit_type="tokens",
                amount_usd=Decimal(amount),
                amount_gbp=Decimal(amount),
                fx_rate=Decimal(1),
            )
            for amount in costs
        )
        await session.commit()


def _row_of(page: str, request_id: uuid.UUID) -> str:
    """The list's table row for one request, so an assertion cannot pass on another row."""
    start = page.index(f'href="/requests/{request_id}"')
    return page[page.rindex("<tr", 0, start) : page.index("</tr>", start)]


class TestTheDestructiveRoutesAreCsrfProtected:
    """Three POST routes were added, and a state-changing POST without this check is a
    cross-site request forgery waiting to be written. The archive one matters least and is
    tested anyway: the pattern is what has to hold, not the blast radius of one route.
    """

    @pytest.mark.parametrize("action", ["archive", "restore", "remove"])
    async def test_a_submission_without_a_token_is_refused(self, web, db_engine, action):
        request_id = await a_saved_request(web)

        response = await web.post(f"/requests/{request_id}/{action}")

        assert response.status_code == 403
        assert "security token" in response.text
        assert await count_requests(db_engine) == 1

    @pytest.mark.parametrize("action", ["archive", "restore", "remove"])
    async def test_a_forged_token_is_refused(self, web, db_engine, action):
        request_id = await a_saved_request(web)

        response = await web.post(
            f"/requests/{request_id}/{action}",
            data={"csrf_token": "forged.9999999999.deadbeef"},
        )

        assert response.status_code == 403
        assert await count_requests(db_engine) == 1

    async def test_a_token_from_a_different_key_cannot_remove_a_request(self, web, db_engine):
        """The property that makes this a *signed* double submit: setting the cookie is not
        enough, because the value has to carry this server's signature."""
        request_id = await a_saved_request(web)
        foreign = issue_csrf_token(b"an-entirely-different-signing-key")
        web.cookies.set(CSRF_COOKIE_NAME, foreign)

        response = await web.post(f"/requests/{request_id}/remove", data={"csrf_token": foreign})

        assert response.status_code == 403
        assert await count_requests(db_engine) == 1


class TestArchivingThroughTheList:
    async def test_a_valid_token_archives_and_returns_to_the_list(self, web, db_engine):
        request_id = await a_saved_request(web)
        token = await list_token(web)

        response = await web.post(f"/requests/{request_id}/archive", data={"csrf_token": token})

        assert response.status_code == 303
        assert response.headers["location"] == "/requests"
        assert "Microsoft Corporation" not in (await web.get(LIST)).text
        # Archived, not deleted.
        assert await count_requests(db_engine) == 1

    async def test_restoring_returns_to_the_archive_it_was_restored_from(self, web):
        request_id = await a_saved_request(web)
        await web.post(
            f"/requests/{request_id}/archive", data={"csrf_token": await list_token(web)}
        )

        response = await web.post(
            f"/requests/{request_id}/restore",
            data={"csrf_token": await list_token(web, "/requests?archived=1")},
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/requests?archived=1"
        assert "Microsoft Corporation" in (await web.get(LIST)).text


class TestTheRemovalConfirmation:
    async def test_it_states_what_will_go_and_what_survives(self, web):
        request_id = await a_saved_request(web)

        body = (await web.get(f"/requests/{request_id}/remove")).text

        assert "What will remain" in body, (
            "a page that lists only the destruction overstates it, and a warning that "
            "overstates is one people learn to click through"
        )
        assert "The audit trail" in body
        assert "The spend" in body
        assert "The archived documents" in body

    async def test_a_draft_says_only_the_request_goes(self, web):
        request_id = await a_saved_request(web)

        body = (await web.get(f"/requests/{request_id}/remove")).text

        assert "Nothing has been researched against this request" in body

    async def test_looking_at_it_removes_nothing(self, web, db_engine):
        request_id = await a_saved_request(web)

        await web.get(f"/requests/{request_id}/remove")

        assert await count_requests(db_engine) == 1

    async def test_confirming_removes_it(self, web, db_engine):
        request_id = await a_saved_request(web)
        token = await list_token(web)

        response = await web.post(f"/requests/{request_id}/remove", data={"csrf_token": token})

        assert response.status_code == 303
        assert response.headers["location"] == "/requests"
        assert await count_requests(db_engine) == 0


class TestWhatYouAreBuying:
    """The panel beside the new form (page specification §6): every depth counted and
    priced, the price on the button that buys it, and the decisions a run will ask for.

    **A depth nobody has run is still priced as what it is.** The fallback was one flat
    declared figure for every depth without history of its own, so an operator with ten
    standard runs saw the quick screen at twice the price of the report it is a lighter
    version of — the setting's effect on the estimate shown backwards, which the page
    specification names as a thing the page must not do.
    """

    async def test_every_depth_says_what_it_writes_and_what_it_costs(self, web):
        panel = _panel((await web.get(NEW)).text)

        for depth, sections in (("quick", "9 sections"), ("standard", "18 sections")):
            row = _depth_row(panel, depth)
            assert sections in row
            assert "about £" in row

    async def test_a_depth_is_priced_from_its_own_finished_runs(self, web, db_engine):
        await _finished_runs(web, db_engine, "standard", ("2.00", "4.00", "6.00"))

        panel = _panel((await web.get(NEW)).text)

        assert _price(panel, "standard") == Decimal("4.00")
        assert "£2.00 to £6.00 over your 3 finished runs at this depth" in panel

    async def test_a_depth_nobody_has_run_is_scaled_from_the_one_they_have(self, web, db_engine):
        await _finished_runs(web, db_engine, "standard", ("2.00", "4.00", "6.00"))

        panel = _panel((await web.get(NEW)).text)

        # The factor the drafting budgets scale by: quick reads and writes 60% as much.
        assert _price(panel, "quick") == Decimal("2.40")
        assert _price(panel, "full") == Decimal("5.60")
        assert "Scaled from your 3 finished standard runs" in panel

    async def test_with_no_history_at_all_the_depths_still_differ(self, web):
        panel = _panel((await web.get(NEW)).text)

        assert _price(panel, "quick") < _price(panel, "standard") < _price(panel, "full")
        assert "The workflow&#39;s own estimate" in panel

    async def test_the_blank_form_opens_at_standard_and_the_button_says_its_price(self, web):
        page = (await web.get(NEW)).text

        assert re.search(r'value="standard"\s+checked', page)
        start = page.index('id="commission"')
        button = page[start : page.index("</button>", start)]
        shown = re.findall(r'<span data-branch="(\w+)" >([^<]+)</span>', button)
        assert shown == [("standard", f"Commission &mdash; about £{_price(page, 'standard')}")]

    async def test_the_decisions_are_counted_from_the_gates_themselves(self, web):
        panel = _panel((await web.get(NEW)).text)

        assert 'id="decisions-asked">2 to 7<' in panel
        assert "Every run stops for the plan and the report" in panel


class TestCommissioning:
    """*Commission* saves the request and starts its run in one press; *Save as a draft*
    stays the other button and spends nothing. Both are the same POST, told apart by the
    button that sent it, so the form has one set of rules whichever was pressed."""

    async def test_commission_starts_the_run_and_opens_its_console(self, web, db_engine, enqueued):
        token = await fresh_token(web)

        response = await web.post(NEW, data=valid_form(csrf_token=token, intent="commission"))

        assert response.status_code == 303
        location = response.headers["location"]
        assert re.fullmatch(r"/runs/[0-9a-f-]{36}", location)
        job_id = location.rsplit("/", 1)[-1]
        assert enqueued.job_ids == [job_id]
        async with async_sessionmaker(bind=db_engine)() as session:
            job = await session.get(Job, uuid.UUID(job_id))
        assert job is not None

    async def test_saving_as_a_draft_starts_nothing(self, web, db_engine, enqueued):
        token = await fresh_token(web)

        response = await web.post(NEW, data=valid_form(csrf_token=token))

        assert response.headers["location"].startswith("/requests/")
        assert enqueued.job_ids == []
        async with async_sessionmaker(bind=db_engine)() as session:
            assert (await session.scalars(select(Job))).all() == []

    async def test_a_subject_the_register_refuses_is_refused_before_anything_is_saved(
        self, api_settings, db_engine, fake_redis, web, enqueued
    ):
        refusing = Registers(
            sec_client=_RefusingRegister(),  # type: ignore[arg-type]
            companies_house_client=_RefusingRegister(),  # type: ignore[arg-type]
        )
        app = build_app(api_settings, engine=db_engine, redis=fake_redis, registers=refusing)
        async for client in client_for(app):
            token = await fresh_token(client)
            response = await client.post(
                NEW, data=valid_form(csrf_token=token, intent="commission")
            )

        assert response.status_code == 422
        assert _REFUSAL in response.text
        # The form as the operator left it, ready to correct and press again.
        assert "How durable is the Azure margin?" in response.text
        assert enqueued.job_ids == []
        assert await count_requests(db_engine) == 0

    async def test_a_company_with_a_current_report_is_offered_its_refresh_first(
        self, web, db_engine, enqueued
    ):
        """Page specification §6: a warning band offering the refresh at its lower price,
        before a new report is bought — a question, so nothing is saved or started."""
        report_id = await _a_current_report(web, db_engine)
        before = await count_requests(db_engine)
        token = await fresh_token(web)

        response = await web.post(NEW, data=valid_form(csrf_token=token, intent="commission"))

        assert response.status_code == 200
        offer = response.text[response.text.index('id="refresh-offer"') :]
        assert "Microsoft Corporation (MSFT) already has a current report" in offer
        assert f'action="/reports/{report_id}/refresh"' in offer
        assert "Refresh — about £" in offer
        assert 'value="commission_new"' in offer
        assert await count_requests(db_engine) == before
        assert enqueued.job_ids == []

    async def test_a_new_report_anyway_is_commissioned_without_asking_again(
        self, web, db_engine, enqueued
    ):
        await _a_current_report(web, db_engine)
        token = await fresh_token(web)

        response = await web.post(NEW, data=valid_form(csrf_token=token, intent="commission_new"))

        assert response.status_code == 303
        assert re.fullmatch(r"/runs/[0-9a-f-]{36}", response.headers["location"])
        assert len(enqueued.job_ids) == 1

    async def test_a_report_that_is_no_longer_current_offers_nothing(
        self, web, db_engine, enqueued
    ):
        """A withdrawn report answers nothing about the company now, so there is nothing
        to refresh and the commission goes ahead."""
        await _a_current_report(web, db_engine, withdrawn=True)
        token = await fresh_token(web)

        response = await web.post(NEW, data=valid_form(csrf_token=token, intent="commission"))

        assert response.status_code == 303
        assert len(enqueued.job_ids) == 1


async def _a_current_report(web, db_engine, *, withdrawn: bool = False) -> uuid.UUID:
    """An approved report on the form's own company, from a request saved through it."""
    request_id = uuid.UUID(await a_saved_request(web))
    await _a_run(db_engine, request_id, JobStatus.SUCCEEDED, costs=("5.00",))
    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    async with factory() as session:
        request = await session.get(ResearchRequest, request_id)
        job = await session.scalar(select(Job).where(Job.work_order_id == request_id))
        approved = datetime(2026, 9, 12, 10, 0, tzinfo=UTC)
        report = Report(
            job_id=job.id,
            request_id=request_id,
            as_of_date=request.work_order.as_of_date,
            content={"markdown": "approved"},
            content_hash="c" * 64,
            approved_at=approved,
            immutable=True,
            superseded_at=approved + timedelta(days=1) if withdrawn else None,
            supersession_reason="Found wrong after approval." if withdrawn else None,
        )
        session.add(report)
        await session.commit()
        return report.id


class TestTheSubjectIsNamedBeforeSubmit:
    """Page specification §6: the resolved name is on the page before anything is saved, so
    the operator sees which company a ticker names before paying to research it."""

    async def test_a_ticker_the_register_knows_is_named_with_the_register(self, web):
        response = await web.get(RESOLVE, params={"ticker": "msft", "exchange": "NASDAQ"})

        assert response.status_code == 200
        assert 'data-resolved="yes"' in response.text
        assert "SEC EDGAR lists" in response.text
        assert "on NASDAQ as" in response.text

    async def test_a_ticker_it_does_not_know_gets_the_registers_own_refusal(
        self, api_settings, db_engine, fake_redis, web
    ):
        refusing = Registers(
            sec_client=_RefusingRegister(),  # type: ignore[arg-type]
            companies_house_client=_RefusingRegister(),  # type: ignore[arg-type]
        )
        app = build_app(api_settings, engine=db_engine, redis=fake_redis, registers=refusing)
        async for client in client_for(app):
            response = await client.get(RESOLVE, params={"ticker": "ZZZZ", "exchange": "NYSE"})

        assert 'data-resolved="no"' in response.text
        assert _REFUSAL in response.text

    @pytest.mark.parametrize(
        "params",
        [{"ticker": "MSFT"}, {"exchange": "NASDAQ"}, {"ticker": "MSFT", "exchange": "OTC"}],
    )
    async def test_an_incomplete_pair_is_answered_with_nothing(self, web, params):
        """Half a question is not asked of the register, and a venue the form does not
        offer is not either: the form's own validation says what is wrong with it."""
        response = await web.get(RESOLVE, params=params)

        assert response.status_code == 200
        assert "data-resolved" not in response.text

    async def test_the_form_asks_as_the_ticker_or_exchange_changes(self, web):
        page = (await web.get(NEW)).text

        start = page.index('id="resolved-subject"')
        asking = page[start : page.index(">", start)]
        assert 'hx-get="/requests/resolve"' in asking
        assert "change from:#ticker" in asking
        assert "change from:#exchange" in asking

    async def test_asking_the_register_never_disables_commission(self, web):
        """The buttons are disabled for the form's own POST and nothing else. htmx hands
        `hx-disabled-elt` to every element inside the form unless the form withholds it, and
        the register preview asks on every change of the ticker — so a press on *Commission*
        while it asked was lost, which the browser suite caught as a request never made."""
        page = (await web.get(NEW)).text

        start = page.index('id="request-form"')
        form = page[page.rindex("<form", 0, start) : page.index(">", start)]
        assert 'hx-disabled-elt="#submit, #commission"' in form
        assert 'hx-disinherit="hx-disabled-elt"' in form


RESOLVE = "/requests/resolve"
_REFUSAL = "EDGAR lists no company under that ticker."


class _RefusingRegister:
    """A register that recognises nothing, and reaches no network."""

    async def resolve_entity(self, ticker: str, **_: object) -> object:
        raise ValidationError(_REFUSAL)


@pytest.fixture
def enqueued(monkeypatch: pytest.MonkeyPatch) -> _Enqueued:
    """What the form handed to the queue, recorded instead of sent."""
    recorder = _Enqueued()
    monkeypatch.setattr("aer.web.routes.enqueue_run", recorder)
    return recorder


class _Enqueued:
    def __init__(self) -> None:
        self.job_ids: list[str] = []

    async def __call__(self, redis: object, job_id: uuid.UUID) -> str:
        self.job_ids.append(str(job_id))
        return f"task-{job_id}"


async def _finished_runs(web, db_engine, depth: str, costs: tuple[str, ...]) -> None:
    """One finished run per cost, each on its own request at ``depth``."""
    for cost in costs:
        token = await fresh_token(web)
        created = await web.post(NEW, data=valid_form(csrf_token=token, analysis_mode=depth))
        request_id = uuid.UUID(created.headers["location"].rsplit("/", 1)[-1])
        await _a_run(db_engine, request_id, JobStatus.SUCCEEDED, costs=(cost,))


def _panel(page: str) -> str:
    start = page.index('id="what-you-buy"')
    return page[start : page.index("</aside>", start)]


def _depth_row(panel: str, depth: str) -> str:
    start = panel.index(f'data-depth="{depth}"')
    return panel[start : panel.index("</div>", start)]


def _price(page: str, depth: str) -> Decimal:
    """A depth's price as the panel prints it, read back as a number to compare."""
    row = _depth_row(page, depth)
    match = re.search(r"about £([0-9,.]+)", row)
    assert match, f"no price for {depth}"
    return Decimal(match.group(1).replace(",", ""))
