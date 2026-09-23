"""A figure this platform computed says so, where a reader meets it.

ADR 0114 derives a bank's total revenue from net interest income and noninterest income,
because a bank publishes no revenue caption and the ASC 606 line is a correct fact and the
wrong answer — M&T's published net margin was 172.1% on it. The row that fixed it is a
*fact*, so a citation of it resolves to a source document, and the document says nothing
of the kind: the reader met "Form 10-K, published…, tier T1_REGULATORY" against a number
no page of that filing contains.

The ADR named this and parked it: *"a derived figure's note say what it is — the sum of two
named captions, each with its own page reference — because the figure appears in no
filing."* This module is that note.

**The note is built from the row's own workings, not from a second reading of the concepts
it names.** A re-render asks what the derivation *did*; asking what those concepts say now
is how a re-render comes to disagree with the report it re-renders (roadmap §3.19.8).
"""

from __future__ import annotations

import itertools
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from aer.config import HouseStyle
from aer.core.enums import FactBasis, JobStatus, Provider, SourceTier, UserRole
from aer.db.models import (
    Artefact,
    Company,
    FinancialFact,
    Job,
    ReportSection,
    SectionDefinition,
    SectionStatus,
    SourceDocument,
    User,
)
from aer.render.document import DerivedFootnote, SourceFootnote, assemble_document
from aer.render.html import render_html
from aer.render.markdown import serialise_markdown
from aer.sections.render import CitationRef, render_section
from tests.request_fixtures import research_request

RETRIEVED_AT = datetime(2026, 2, 20, 12, 0, tzinfo=UTC)
GENERATED_AT = datetime(2026, 2, 21, 9, 30, tzinfo=UTC)

# M&T's own figures, to the dollar, as the live run of 17 September 2026 recorded them.
NET_INTEREST = Decimal("6948000000")
NONINTEREST = Decimal("2742000000")
TOTAL = NET_INTEREST + NONINTEREST

_SERIAL = itertools.count(1)

_CONTRACT: dict[str, Any] = {
    "type": "object",
    "properties": {
        "figures": {
            "type": "array",
            "title": "Figures",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "value": {"type": "string"},
                    "unit": {"type": "string"},
                    "source_document_id": {"type": "string"},
                    "financial_fact_id": {"type": "string"},
                },
            },
        },
    },
}


async def _scene(
    session: AsyncSession, *, components_share_a_document: bool = True
) -> dict[str, Any]:
    """One run whose only figure is derived, cited through a section's content."""
    serial = next(_SERIAL)
    user = User(
        email=f"derived-{serial}@example.invalid", display_name="Reader", role=UserRole.OWNER
    )
    session.add(user)
    await session.flush()

    company = Company(
        name=f"M&T BANK CORP {serial}",
        cik=f"{serial:010d}",
        ticker=f"MTB{serial:04d}",
        exchange="NYSE",
    )
    session.add(company)
    await session.flush()

    request = research_request(
        user_id=user.id,
        company_id=company.id,
        company_name="M&T Bank Corporation",
        ticker="MTB",
        exchange="NYSE",
        as_of_date=date(2026, 2, 20),
        base_currency="USD",
        reporting_currency="USD",
        investment_horizon_months=12,
        max_cost_gbp="2.50",
    )
    session.add(request)
    await session.flush()

    job = Job(
        work_order_id=request.id,
        workflow_version="derived_note_v1",
        code_version="derivedcode123",
        status=JobStatus.RUNNING,
        started_at=RETRIEVED_AT,
    )
    session.add(job)
    await session.flush()

    documents: list[SourceDocument] = []
    for index, title in enumerate(("Form 10-K, fiscal 2025", "Fourth-quarter results release")):
        artefact = Artefact(
            sha256=uuid.uuid4().hex + uuid.uuid4().hex,
            media_type="text/html",
            size_bytes=64,
            storage_key=f"derived/{serial}/{index}",
        )
        session.add(artefact)
        await session.flush()
        document = SourceDocument(
            work_order_id=request.id,
            job_id=job.id,
            company_id=company.id,
            artefact_id=artefact.id,
            url=f"https://www.sec.gov/Archives/edgar/data/36270/mtb-{serial}-{index}.htm",
            title=title,
            publisher="US Securities and Exchange Commission",
            provider=Provider.SEC_EDGAR,
            source_tier=SourceTier.T1_REGULATORY,
            retrieved_at=RETRIEVED_AT,
            publication_date=date(2026, 2, 18),
        )
        session.add(document)
        documents.append(document)
    await session.flush()

    second = documents[0] if components_share_a_document else documents[1]
    fact = FinancialFact(
        company_id=company.id,
        source_document_id=documents[0].id,
        concept="revenue",
        value=TOTAL,
        unit="USD",
        scale=0,
        period_start=date(2025, 1, 1),
        period_end=date(2025, 12, 31),
        fiscal_year=2025,
        fiscal_period="FY",
        filed_date=date(2026, 2, 18),
        form="10-K",
        basis=FactBasis.DERIVED,
        derivation={
            "formula": "revenue = net_interest_income + noninterest_income",
            "sector": "banks",
            "code_version": "derivedcode123456",
            "inputs": [
                {
                    "fact_id": str(uuid.uuid4()),
                    "concept": "net_interest_income",
                    "value": str(NET_INTEREST),
                    "unit": "USD",
                    "period_end": "2025-12-31",
                    "source_document_id": str(documents[0].id),
                },
                {
                    "fact_id": str(uuid.uuid4()),
                    "concept": "noninterest_income",
                    "value": str(NONINTEREST),
                    "unit": "USD",
                    "period_end": "2025-12-31",
                    "source_document_id": str(second.id),
                },
            ],
        },
    )
    session.add(fact)
    await session.flush()

    definition = SectionDefinition(
        key=f"derived_{serial}",
        version=1,
        origin="builtin",
        title="Revenue",
        position=Decimal(100),
        required=False,
        output_contract=_CONTRACT,
        evidence_policy={"min_sources": 0, "requires_primary": False},
        token_budget=1000,
        allowed_tools=[],
        applicability={},
    )
    session.add(definition)
    await session.flush()

    session.add(
        ReportSection(
            job_id=job.id,
            section_definition_id=definition.id,
            section_key=definition.key,
            position=definition.position,
            status=SectionStatus.GENERATED,
            content={
                "figures": [
                    {
                        "label": "Total revenue",
                        "value": str(TOTAL),
                        "unit": "USD",
                        "source_document_id": str(documents[0].id),
                        "financial_fact_id": str(fact.id),
                    }
                ]
            },
        )
    )
    await session.flush()

    return {
        "session": session,
        "job": job,
        "request": request,
        "company": company,
        "fact": fact,
        "documents": documents,
    }


async def _document(scene: dict[str, Any]) -> Any:
    return await assemble_document(
        scene["session"],
        job=scene["job"],
        request=scene["request"],
        company=scene["company"],
        generated_at=GENERATED_AT,
    )


class TestTheMarkerResolvesToTheDerivation:
    async def test_the_note_is_the_derivation_not_the_document(
        self, db_session: AsyncSession
    ) -> None:
        """The whole of it: the marker used to resolve to a filing that does not contain
        the number, and a reader following it would have searched that filing in vain."""
        document = await _document(await _scene(db_session))

        note = document.footnotes[0]
        assert isinstance(note, DerivedFootnote)
        assert note.label == "revenue"
        assert note.period_label == "FY2025"
        assert [one.label for one in note.inputs] == [
            "net interest income",
            "noninterest income",
        ]

    async def test_the_sentence_names_the_sum_and_refuses_the_filing(
        self, db_session: AsyncSession
    ) -> None:
        document = await _document(await _scene(db_session))
        note = document.footnotes[0]
        assert isinstance(note, DerivedFootnote)

        assert note.statement.startswith("Revenue for FY2025 is ")
        assert "being net interest income of " in note.statement
        assert " plus noninterest income of " in note.statement

    async def test_one_document_is_named_once(self, db_session: AsyncSession) -> None:
        """The two halves of a bank's revenue are ordinarily two lines of one filing, and
        naming it twice would read as two pieces of evidence."""
        document = await _document(await _scene(db_session))
        note = document.footnotes[0]
        assert isinstance(note, DerivedFootnote)

        assert [title for title, _ in note.sources] == ["Form 10-K, fiscal 2025"]

    async def test_two_documents_are_both_named(self, db_session: AsyncSession) -> None:
        document = await _document(await _scene(db_session, components_share_a_document=False))
        note = document.footnotes[0]
        assert isinstance(note, DerivedFootnote)

        assert [title for title, _ in note.sources] == [
            "Form 10-K, fiscal 2025",
            "Fourth-quarter results release",
        ]

    async def test_an_as_reported_figure_keeps_the_document_note(
        self, db_session: AsyncSession
    ) -> None:
        """The control. Only a derived row takes a note of its own; a figure a filer
        stated is still described by the filing that stated it."""
        scene = await _scene(db_session)
        scene["fact"].basis = FactBasis.AS_REPORTED
        scene["fact"].derivation = None
        await db_session.flush()

        document = await _document(scene)

        assert isinstance(document.footnotes[0], SourceFootnote)


class TestBothNotationsSayIt:
    async def test_the_markdown_note_names_the_sum_and_its_filing(
        self, db_session: AsyncSession
    ) -> None:
        document = await _document(await _scene(db_session))

        markdown = serialise_markdown(document)

        assert "**Derived, not reported.** Revenue for FY2025 is " in markdown
        assert "Each component is stated in [Form 10-K, fiscal 2025](" in markdown
        # The code version, as a calculation's note carries one: the sum is this
        # platform's arithmetic and a reader is entitled to know which version did it.
        assert "(code version `derivedcode1`)" in markdown
        # The reference the note replaces: no tier, no retrieval date, nothing that would
        # read as the filing having stated the figure. Asserted on the words a source note
        # now carries — "a regulatory filing" — rather than on the tier code it used to,
        # which would pass on a page that had simply stopped saying anything.
        notes = markdown.split("## Notes")[1].split("## Sources")[0]
        assert SourceTier.T1_REGULATORY.spoken not in notes
        assert "retrieved" not in notes

    async def test_the_html_note_says_the_same_thing(self, db_session: AsyncSession) -> None:
        document = await _document(await _scene(db_session))

        html = render_html(document)

        assert "<strong>Derived, not reported.</strong> Revenue for FY2025 is " in html
        assert "Each component is stated in <a" in html

    async def test_the_marker_hover_says_it_too(self, db_session: AsyncSession) -> None:
        """A reader who never reaches the notes still learns it, which is the one place
        the old rendering was actively misleading rather than merely thin."""
        document = await _document(await _scene(db_session))

        html = render_html(document)

        assert 'title="Derived, not reported.' in html


class TestTheFactIdRidesTheCitationWithoutMovingAMarker:
    """``CitationRef.fact_id`` is deliberately outside the dataclass's identity."""

    def test_two_refs_differing_only_in_fact_still_share_a_marker(self) -> None:
        one = CitationRef(kind="source_document", identifier="d", label="Revenue", fact_id="a")
        two = CitationRef(kind="source_document", identifier="d", label="Revenue", fact_id="b")

        assert one == two

    def test_a_calculation_marker_carries_no_fact(self) -> None:
        """A calculation's note already walks to its own inputs; the question "did a filer
        say this?" belongs to the document's marker and to no other."""
        rendered = render_section(
            title="Figures",
            key="figures",
            content={
                "figures": [
                    {
                        "label": "Margin",
                        "value": "0.29",
                        "unit": "ratio",
                        "calculation_id": str(uuid.UUID(int=1)),
                        "source_document_id": str(uuid.UUID(int=2)),
                        "financial_fact_id": str(uuid.UUID(int=3)),
                    }
                ]
            },
            contract={
                "type": "object",
                "properties": {
                    "figures": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "label": {"type": "string"},
                                "value": {"type": "string"},
                                "unit": {"type": "string"},
                                "calculation_id": {"type": "string"},
                                "source_document_id": {"type": "string"},
                                "financial_fact_id": {"type": "string"},
                            },
                        },
                    }
                },
            },
            style=HouseStyle(),
        )

        assert rendered.fragments
        by_kind = {ref.kind: ref.fact_id for ref in rendered.citations}
        assert by_kind["calculation"] == ""
        assert by_kind["source_document"] == str(uuid.UUID(int=3))
