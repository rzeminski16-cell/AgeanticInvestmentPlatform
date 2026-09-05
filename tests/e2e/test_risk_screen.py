"""The risk page, walked once: the figures, a scenario stated and withdrawn, the reading.

`test_risk_service.py` proves the figures and drives the pages in-process. Nothing there
proves a person can do it — that the page opens on the book's own figures with their
lineage, that the scenario form really produces a profit and loss in a browser, and that
the analyst's reading is shown beside the figures it read.

**No worker and no real model.** The reading is run in the seed against the fake provider,
answering with a commentary that names only figures the block holds, because what is under
test is the surface that shows it.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from aer.agents.risk_analyst import RiskCommentary
from aer.config import Settings
from aer.core.enums import TransactionKind
from aer.db.models import Portfolio, PriceBar, Security, User
from aer.providers.fake import FakeProvider
from aer.providers.router import Router
from aer.services import risk as risk_service
from aer.storage.local import LocalArtefactStore
from tests.db_fixtures import run_async
from tests.portfolio_fixtures import AS_OF, daily_bars, funded, trade
from tests.schema_guard import refuse_unanswerable_schema

pytestmark = [pytest.mark.e2e, pytest.mark.integration]


async def _seed(database_url: str, tmp_path: Path) -> None:
    """A book holding Barclays with a run of closes, and the analyst's reading of it."""
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            user = await session.scalar(select(User).limit(1))
            assert user is not None, "the reset seeds a user"
            book = Portfolio(user_id=user.id, name="My book", base_currency="GBP")
            barc = Security(
                ticker="BARC", exchange="LSE", provider_symbol="BARC.LSE", quote_currency="GBX"
            )
            session.add_all([book, barc])
            await session.flush()
            session.add(
                PriceBar(
                    security_id=barc.id,
                    bar_date=AS_OF,
                    open=Decimal("248"),
                    high=Decimal("252"),
                    low=Decimal("247"),
                    close=Decimal("250"),
                )
            )
            scene = {"user": user, "portfolio": book, "barc": barc, "document": None}
            await funded(session, scene)
            await trade(
                session,
                scene,
                kind=TransactionKind.BUY,
                security=barc,
                quantity="100",
                price="250",
                currency="GBX",
            )
            await daily_bars(session, barc, until=AS_OF, days=40)

            settings = Settings(
                http_user_agent="Test test@example.invalid", artefact_root=tmp_path / "artefacts"
            )
            await risk_service.run_reading(
                session,
                settings=settings,
                provider=FakeProvider(
                    {
                        "RiskCommentary": RiskCommentary(
                            exposure=(
                                "Everything priced is one London bank, so the sector and "
                                "country bands are the same slice under two names."
                            ),
                            movement=(
                                "The book's movement is that one holding's, damped by the "
                                "cash beside it."
                            ),
                        )
                    },
                    inspect_schema=refuse_unanswerable_schema,
                ),
                router=Router(settings),
                store=LocalArtefactStore(
                    settings.artefact_root, max_bytes=settings.max_artefact_bytes
                ),
                user=user,
                portfolio=book,
                as_of=AS_OF,
            )
            await session.commit()
    finally:
        await engine.dispose()


class TestTheRiskPage:
    def test_figures_a_scenario_and_the_reading(
        self, page: Page, live_server: str, database_url: str, tmp_path: Path
    ) -> None:
        run_async(_seed(database_url, tmp_path))

        # From the launcher, the book's own figures with their lineage, and the reading.
        page.goto(live_server)
        page.locator('[data-tool="risk"] [data-field="action"]').click()
        page.wait_for_url("**/risk**")
        expect(page.locator('[data-figure="annualised-volatility"]')).to_be_visible()
        # Computed on the way to the page and persisted nowhere, so no calculation link is
        # offered: a link to a row that does not exist is a dead link.
        expect(page.locator('[data-figure="annualised-volatility"] a')).to_have_count(0)
        expect(page.locator('[data-holding="BARC"]')).to_have_attribute("data-measured", "yes")
        expect(page.locator('[data-commentary="exposure"]')).to_contain_text("one London bank")
        expect(page.get_by_text("No scenario stated")).to_be_visible()

        # A scenario stated on the form is applied to the book as it stands.
        page.fill("#name", "Everything down a fifth")
        page.select_option("#kind_1", "book")
        page.fill("#shock_1", "-20")
        page.click("#state")
        page.wait_for_url("**/risk")
        row = page.locator("[data-scenario]")
        expect(row).to_have_count(1)
        expect(row).to_have_attribute("data-loss", "yes")
        expect(row.locator('[data-field="pnl"]')).to_contain_text("-50.00 GBP")

        # And withdrawn from the same row.
        row.get_by_role("button", name="Withdraw").click()
        page.wait_for_url("**/risk")
        expect(page.locator("[data-scenario]")).to_have_count(0)

        # The work list asks nothing of a book that has been read and has not traded since.
        page.goto(live_server)
        expect(page.locator('[data-tool="risk"][data-attention]')).to_have_count(0)
