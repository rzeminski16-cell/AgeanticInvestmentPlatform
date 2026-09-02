"""The journal, walked once: decide, then record the trade that carries it out.

`test_decisions.py` proves the record and drives the pages in-process. Nothing there proves
a person can do it — that the journal's form reaches the decision page, that the portfolio
form really offers the decision under "Carries out", and that the decision page then shows
the trade in a browser rather than in an assertion about HTML.

**No worker and no model.** A decision is a document a person writes; there is nothing to
approve and nothing to spend.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from aer.db.models import Company, Portfolio, Security, User
from aer.services import theses as thesis_service
from tests.db_fixtures import run_async

pytestmark = [pytest.mark.e2e, pytest.mark.integration]


async def _seed(database_url: str) -> None:
    """A company, a dealable listing, a book and a thesis: everything a decision acts on."""
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            user = await session.scalar(select(User).limit(1))
            assert user is not None, "the reset seeds a user"
            company = Company(
                name="Contoso plc", ticker="CTSO", exchange="LSE", company_number="01234567"
            )
            session.add(company)
            await session.flush()
            session.add_all(
                [
                    Security(
                        company_id=company.id,
                        ticker="CTSO",
                        exchange="LSE",
                        provider_symbol="CTSO.LSE",
                        name="Contoso plc",
                        quote_currency="GBX",
                    ),
                    Portfolio(user_id=user.id, name="My book", base_currency="GBP"),
                ]
            )
            await thesis_service.write_thesis(
                session, user=user, company=company, title="Contoso keeps compounding"
            )
            await session.commit()
    finally:
        await engine.dispose()


class TestADecisionThenTheTrade:
    def test_decide_then_carry_it_out(
        self, page: Page, live_server: str, database_url: str
    ) -> None:
        run_async(_seed(database_url))

        # From the launcher to the journal, and a decision written before the trade.
        page.goto(live_server)
        page.locator('[data-tool="decisions"] [data-field="action"]').click()
        page.wait_for_url("**/decisions")
        page.select_option("#thesis_id", index=0)
        page.select_option("#action", "buy")
        page.fill("#statement", "Open an initial position.")
        page.fill("#basis", "The FY25 report confirmed the margin structure.")
        page.fill("#security", "CTSO.LSE")
        page.fill("#size_statement", "about 2% of the book")
        page.fill("#horizon_months", "24")
        page.click("#record")
        page.wait_for_url("**/decisions/*")
        decision_url = page.url
        expect(page.locator('[data-field="statement"]')).to_have_text("Open an initial position.")
        expect(page.get_by_text("Not yet carried out")).to_be_visible()

        # The work list says so.
        page.goto(live_server)
        row = page.locator('[data-tool="decisions"][data-attention]')
        expect(row).to_have_count(1)
        expect(row).to_contain_text("You decided to open a position")

        # The trade, recorded on the portfolio form, names the decision.
        page.goto(f"{live_server}/portfolio")
        page.select_option("#kind", "buy")
        page.fill("#security", "CTSO.LSE")
        page.fill("#trade_date", "2026-08-02")
        page.fill("#quantity", "100")
        page.fill("#price", "1250")
        page.fill("#currency", "GBX")
        page.select_option("#decision", index=1)
        page.click("#record-transaction button[type='submit']")
        page.wait_for_url("**/portfolio")

        # And the decision page lists it; the work list no longer asks.
        page.goto(decision_url)
        expect(page.locator('[data-trade="buy"]')).to_contain_text("100")
        expect(page.get_by_text("Not yet carried out")).to_have_count(0)
        page.goto(live_server)
        expect(page.locator('[data-tool="decisions"][data-attention]')).to_have_count(0)
