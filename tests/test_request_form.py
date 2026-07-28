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
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from aer.api.security import CSRF_COOKIE_NAME, issue_csrf_token
from aer.core.enums import UserRole
from aer.db.models import ResearchRequest, User
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
