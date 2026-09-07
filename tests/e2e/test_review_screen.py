"""The review, walked once: a proposal on the work list, confirmed with an amendment, counted.

`test_post_trade.py` proves the pass and drives the pages in-process. Nothing there proves
a person can do it — that the work list's row leads to the proposal, that the form arrives
prefilled with what the reviewer said, that changing the quality really makes the review
say *amended* in a browser rather than in an assertion about HTML.

**No worker and no real model.** The pass is run in the seed against the fake provider,
answering with the draft a reviewer would have written, because what is under test is the
surface that confirms it, not the model that proposed it.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from aer.agents.post_trade_reviewer import PremiseVerdictDraft, ReviewDraft
from aer.config import Settings
from aer.core.enums import DecisionAction, PremiseVerdict, ProcessQuality, TransactionKind
from aer.db.models import Company, Portfolio, Security, Transaction, User
from aer.providers.fake import FakeProvider
from aer.providers.router import Router
from aer.services import decisions as decision_service
from aer.services import post_trade
from aer.services import theses as thesis_service
from aer.storage.local import LocalArtefactStore
from tests.db_fixtures import run_async
from tests.portfolio_fixtures import trade
from tests.schema_guard import refuse_unanswerable_schema

pytestmark = [pytest.mark.e2e, pytest.mark.integration]


async def _seed(database_url: str, tmp_path: Path) -> None:
    """A book with one closed position, a decision behind it, and the reviewer's proposal."""
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            user = await session.scalar(select(User).limit(1))
            assert user is not None, "the reset seeds a user"
            company = Company(
                name="Contoso plc", ticker="CTSO", exchange="LSE", company_number="01234567"
            )
            book = Portfolio(user_id=user.id, name="My book", base_currency="GBP")
            session.add_all([company, book])
            await session.flush()
            security = Security(
                company_id=company.id,
                ticker="CTSO",
                exchange="LSE",
                provider_symbol="CTSO.LSE",
                name="Contoso plc",
                quote_currency="GBX",
            )
            session.add(security)
            await session.flush()
            thesis = await thesis_service.write_thesis(
                session, user=user, company=company, title="Contoso keeps compounding"
            )
            premise = await thesis_service.add_premise(
                session,
                thesis=thesis,
                actor=user,
                statement="Management allocates capital well.",
                basis="Ten years of buybacks below intrinsic value.",
                predicate=None,
                review_by=date(2027, 3, 31),
            )
            loaded = await thesis_service.thesis_of(session, thesis.id, user_id=user.id)
            assert loaded is not None
            decision = await decision_service.record_decision(
                session,
                actor=user,
                thesis=loaded,
                action=DecisionAction.BUY,
                statement="Open an initial position.",
                basis="The FY25 report confirmed the margin structure.",
                security=security,
                size_statement="about 2% of the book",
                horizon_months=24,
                exit_plan="Sell if operating margin falls below 20%.",
                decided_at=datetime(2026, 3, 1, 9, tzinfo=UTC),
            )
            scene = {"portfolio": book, "document": None}
            bought = await trade(
                session,
                scene,
                kind=TransactionKind.BUY,
                security=security,
                quantity="100",
                price="250",
                currency="GBX",
                on=date(2026, 3, 2),
            )
            await trade(
                session,
                scene,
                kind=TransactionKind.SELL,
                security=security,
                quantity="-100",
                price="300",
                currency="GBX",
                on=date(2026, 6, 15),
                at_hour=16,
            )
            transaction = await session.scalar(
                select(Transaction).where(Transaction.attestation_id == bought.id)
            )
            assert transaction is not None
            await decision_service.carry_out(
                session, transaction=transaction, decision=decision, actor=user
            )

            settings = Settings(
                http_user_agent="Test test@example.invalid", artefact_root=tmp_path / "artefacts"
            )
            [episode] = await post_trade.closed_episodes(session, portfolio=book)
            await post_trade.run_review(
                session,
                settings=settings,
                provider=FakeProvider(
                    {
                        "ReviewDraft": ReviewDraft(
                            verdicts=[
                                PremiseVerdictDraft(
                                    premise_id=str(premise.judgement_id),
                                    verdict=PremiseVerdict.HELD,
                                    note="The buyback continued through the half.",
                                )
                            ],
                            process_quality=ProcessQuality.SOUND,
                            basis="The decision was written first, sized, and carried out.",
                            lessons="The exit came at four months against twenty-four.",
                        )
                    },
                    inspect_schema=refuse_unanswerable_schema,
                ),
                router=Router(settings),
                store=LocalArtefactStore(
                    settings.artefact_root, max_bytes=settings.max_artefact_bytes
                ),
                user=user,
                episode=episode,
            )
            await session.commit()
    finally:
        await engine.dispose()


class TestAReviewFromTheWorkList:
    def test_a_proposal_is_confirmed_with_an_amendment(
        self, page: Page, live_server: str, database_url: str, tmp_path: Path
    ) -> None:
        run_async(_seed(database_url, tmp_path))

        # It is on the work list as waiting for you, and the row leads to the proposal.
        page.goto(live_server)
        row = page.locator('[data-tool="review"][data-attention]')
        expect(row).to_have_count(1)
        expect(row).to_contain_text("waiting for you")
        row.get_by_role("link", name="Read the proposal").click()
        page.wait_for_url("**/review/passes/*")

        # The outcome is code's, and it links to its formula.
        expect(page.locator('[data-figure="realised-return"]')).to_contain_text("+20.0%")
        expect(page.locator('[data-figure="realised-return"] a')).to_have_count(1)
        expect(page.locator("#process_quality")).to_have_value("sound")

        # The operator disagrees about the quality, keeps the verdict, and confirms.
        page.select_option("#process_quality", "questionable")
        page.fill("#basis", "The sale followed no part of the exit plan.")
        page.click("#confirm")
        page.wait_for_url("**/review/*")
        assert "/review/passes/" not in page.url
        expect(page.locator('[data-field="quality"]')).to_contain_text("Questionable")
        expect(page.get_by_text("Amended", exact=False).first).to_be_visible()
        expect(page.locator('[data-field="proposed-quality"]')).to_contain_text("Sound")

        # One review is a tally, not a proportion.
        page.goto(f"{live_server}/analytics")
        cells = page.locator('[data-statistic="process-against-outcome"]')
        expect(cells).to_have_attribute("data-count", "1")
        expect(cells).to_have_attribute("data-finding", "no")
        # The four cells two by two: the amended quality with a gain is the off-diagonal
        # cell the page exists to make reachable, and it is the cell that reads 1.
        expect(cells.locator('[data-part="flawed-or-questionable-process-gain"]')).to_have_text("1")
        expect(cells.locator('[data-part="sound-process-gain"]')).to_have_text("0")
        expect(cells.get_by_role("columnheader", name="Loss")).to_be_visible()

        # The list shows it reviewed, and the work list asks nothing more.
        page.goto(f"{live_server}/review")
        expect(page.locator('[data-state="reviewed"]')).to_have_count(1)
        page.goto(live_server)
        expect(page.locator('[data-tool="review"][data-attention]')).to_have_count(0)
