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

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from aer.api.security import CSRF_COOKIE_NAME, issue_csrf_token
from aer.core.enums import UserRole
from aer.db.models import JobCancellation, ResearchRequest, User
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
        "as_of_date": "2026-07-01",
        "base_currency": "USD",
        "reporting_currency": "",
        "investment_horizon_months": "36",
        "horizon_label": "Through the next capex cycle",
        "analysis_mode": "full",
        "point_in_time": "true",
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
        assert "New research request" in response.text

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

    async def test_the_as_of_input_cannot_be_set_past_today(self, web):
        # A browser-side convenience, not the rule. The rule is server-side and tested
        # separately; this stops the operator picking a date that will be rejected.
        today = datetime.now(UTC).date().isoformat()
        assert f'max="{today}"' in (await web.get(NEW)).text

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

    async def test_an_unchecked_point_in_time_box_is_stored_as_false(self, web, db_engine):
        # An unchecked checkbox is simply absent from the submission. Reading it as
        # "missing, therefore leave the default" would make the box impossible to turn off.
        token = await fresh_token(web)
        form = valid_form(csrf_token=token)
        del form["point_in_time"]
        await web.post(NEW, data=form)

        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as session:
            row = await session.scalar(select(ResearchRequest))
        assert row.point_in_time is False

    async def test_the_new_request_appears_in_the_list(self, web):
        token = await fresh_token(web)
        await web.post(NEW, data=valid_form(csrf_token=token))

        listing = await web.get("/requests")
        assert "Microsoft Corporation" in listing.text


class TestRejectedSubmission:
    async def test_a_future_as_of_date_is_rejected_and_nothing_is_created(self, web, db_engine):
        tomorrow = (datetime.now(UTC).date() + timedelta(days=1)).isoformat()
        token = await fresh_token(web)

        response = await web.post(NEW, data=valid_form(csrf_token=token, as_of_date=tomorrow))

        assert response.status_code == 422
        assert "in the future" in response.text
        assert await count_requests(db_engine) == 0

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

        assert "What survives" in body, (
            "a page that lists only the destruction overstates it, and a warning that "
            "overstates is one people learn to click through"
        )
        assert "The audit trail." in body
        assert "The spend." in body
        assert "The archived documents." in body

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
