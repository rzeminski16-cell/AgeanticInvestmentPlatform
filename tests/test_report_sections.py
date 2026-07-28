"""Sections are data, not code — proved by adding one.

:class:`TestAThirdSection` is the test the whole design exists for. It inserts one
``section_definitions`` row and asserts the rendered report gains a section, in the right
place, with footnote numbering that stays correct — **with no change to any Python file**.
If that ever stops being true, Phase 4's user-authored sections are not implementable and
the architecture needs revisiting rather than patching.

:class:`TestNoSectionKeyIsHardcoded` guards the same property from the other side: it reads
the source tree and fails if a section key appears outside the seed migration. A renderer
that special-cased ``executive_summary`` would pass every test above and quietly make the
next section impossible.
"""

from __future__ import annotations

import ast
import re
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.config import Settings
from aer.core.enums import Decision, GateKind
from aer.db.models import Artefact, Job, JobStep, Report, SectionDefinition, SourceDocument
from aer.providers.fake import FakeProvider
from aer.services import approvals as approval_service
from aer.services import runs as run_service
from aer.storage.local import LocalArtefactStore
from tests.workflow_fixtures import StubSecClient, seed_job, seed_request, seed_user

pytestmark = pytest.mark.anyio

SRC_ROOT = Path(__file__).resolve().parent.parent / "src"
SEED_MIGRATION = (
    Path(__file__).resolve().parent.parent
    / "migrations"
    / "versions"
    / "0006_agents_costs_prompts_sections.py"
)

# The keys the seed migration inserts. Named here so the source scan below can look for
# them; this list is test data, not a section registry.
SEEDED_KEYS = ("executive_summary", "historical_financial_analysis")

# What the third section is called. Deliberately nothing like the built-in two, so a
# renderer that happened to handle those by name could not accidentally handle this.
THIRD_KEY = "competitive_position"
THIRD_TITLE = "Competitive Position"

THIRD_CONTRACT: dict[str, Any] = {
    "type": "object",
    "title": THIRD_TITLE,
    "required": ["commentary"],
    "properties": {
        "commentary": {"type": "string", "title": "Commentary"},
        "observations": {"type": "array", "title": "Observations", "items": {"type": "string"}},
        "figures": {
            "type": "array",
            "title": "Figures",
            "items": {
                "type": "object",
                "required": ["label", "value", "unit"],
                "properties": {
                    "label": {"type": "string"},
                    "value": {"type": "string"},
                    "unit": {"type": "string"},
                    "calculation_id": {"type": "string"},
                    "source_document_id": {"type": "string"},
                },
            },
        },
    },
}


async def insert_third_section(session: AsyncSession, *, position: Decimal) -> SectionDefinition:
    """The entire mechanism for adding a section: one INSERT."""
    definition = SectionDefinition(
        key=THIRD_KEY,
        version=1,
        origin="builtin",
        title=THIRD_TITLE,
        position=position,
        required=True,
        output_contract=THIRD_CONTRACT,
        evidence_policy={"min_sources": 1, "requires_primary": True},
        token_budget=2000,
        allowed_tools=[],
        applicability={},
    )
    session.add(definition)
    await session.flush()
    return definition


async def run_to_report(
    session: AsyncSession,
    *,
    settings: Settings,
    provider: FakeProvider,
    store: LocalArtefactStore,
    sec_client: StubSecClient,
    job: Job,
    user: Any,
) -> Report:
    """Drive a whole run, approving both gates, and return the report it produced."""
    for gate, step in ((GateKind.PLAN, "plan"), (GateKind.FINAL, "draft")):
        await run_service.execute(
            session,
            job=job,
            settings=settings,
            provider=provider,
            store=store,
            sec_client=sec_client,
        )
        row = await session.scalar(
            select(JobStep).where(JobStep.job_id == job.id, JobStep.step_key == step)
        )
        assert row is not None
        await approval_service.record_decision(
            session,
            job=job,
            gate=gate,
            decision=Decision.APPROVED,
            actor=user,
            payload_hash=str((row.output_ref or {})["payload_hash"]),
        )

    await run_service.execute(
        session,
        job=job,
        settings=settings,
        provider=provider,
        store=store,
        sec_client=sec_client,
    )

    report = await session.scalar(select(Report).where(Report.job_id == job.id))
    assert report is not None
    return report


@pytest.fixture
async def run_context(
    db_session: AsyncSession,
    workflow_settings: Settings,
    workflow_store: LocalArtefactStore,
    sec_client: StubSecClient,
    provider: FakeProvider,
) -> dict[str, Any]:
    user = await seed_user(db_session)
    request = await seed_request(db_session, user=user)
    job = await seed_job(db_session, request=request)
    return {
        "session": db_session,
        "user": user,
        "job": job,
        "settings": workflow_settings,
        "store": workflow_store,
        "sec_client": sec_client,
        "provider": provider,
    }


def headings(markdown: str) -> list[str]:
    """The document's ``##`` headings, in order."""
    return re.findall(r"^## (.+)$", markdown, flags=re.MULTILINE)


def footnote_markers(markdown: str) -> list[int]:
    """Every ``[^n]`` marker in the body, in the order it appears."""
    body = markdown.split("\n## Notes\n", maxsplit=1)[0]
    return [int(number) for number in re.findall(r"\[\^(\d+)\]", body)]


def footnote_definitions(markdown: str) -> list[int]:
    return [int(number) for number in re.findall(r"^\[\^(\d+)\]:", markdown, flags=re.MULTILINE)]


class TestTheBaselineReport:
    """What the two seeded sections produce, so the third has something to change."""

    @pytest.fixture
    async def report(self, run_context: dict) -> Report:
        return await run_to_report(**_run_args(run_context))

    async def test_it_has_both_seeded_sections(self, report: Report) -> None:
        assert report.content["sections"] == list(SEEDED_KEYS)

    async def test_the_headings_are_in_position_order(self, report: Report) -> None:
        found = headings(report.content["markdown"])
        assert found.index("Executive Summary") < found.index("Historical Financial Analysis")

    async def test_every_marker_has_a_definition(self, report: Report) -> None:
        markdown = report.content["markdown"]
        assert footnote_markers(markdown)
        assert set(footnote_markers(markdown)) <= set(footnote_definitions(markdown))

    async def test_the_footnotes_are_numbered_from_one_without_gaps(self, report: Report) -> None:
        defined = footnote_definitions(report.content["markdown"])
        assert defined == list(range(1, len(defined) + 1))


class TestAThirdSection:
    """One INSERT, no code change. The property Phase 4 depends on."""

    @pytest.fixture
    async def report(self, run_context: dict) -> Report:
        # 150: between the seeded 100 and 200. The sparse numbering is the whole reason a
        # section can be slotted in without renumbering anything.
        await insert_third_section(run_context["session"], position=Decimal(150))
        return await run_to_report(**_run_args(run_context))

    async def test_the_report_gains_the_section(self, report: Report) -> None:
        assert THIRD_KEY in report.content["sections"]

    async def test_it_lands_between_the_two_built_in_sections(self, report: Report) -> None:
        assert report.content["sections"] == [
            "executive_summary",
            THIRD_KEY,
            "historical_financial_analysis",
        ]

    async def test_its_heading_appears_in_the_right_place(self, report: Report) -> None:
        found = headings(report.content["markdown"])
        assert found.index("Executive Summary") < found.index(THIRD_TITLE)
        assert found.index(THIRD_TITLE) < found.index("Historical Financial Analysis")

    async def test_its_contract_supplies_the_sub_headings(self, report: Report) -> None:
        """No template exists for this section. The headings come from the JSON Schema."""
        markdown = report.content["markdown"]
        assert "### Commentary" in markdown
        assert "### Observations" in markdown

    async def test_the_footnotes_renumber_across_the_whole_document(self, report: Report) -> None:
        """A reader chasing ``[^3]`` must find the third marker in the report.

        The new section cites a figure, so every marker after it shifts. Numbering that
        restarted per section would leave two footnotes called ``[^1]``.
        """
        markdown = report.content["markdown"]
        markers = footnote_markers(markdown)

        assert markers == sorted(markers), "markers must appear in ascending order"
        assert footnote_definitions(markdown) == list(
            range(1, len(footnote_definitions(markdown)) + 1)
        )
        assert set(markers) <= set(footnote_definitions(markdown))

    async def test_it_produced_more_footnotes_than_the_two_section_report(
        self, report: Report, run_context: dict
    ) -> None:
        """Proves the third section really cited something rather than rendering empty."""
        assert len(footnote_definitions(report.content["markdown"])) >= 2

    async def test_a_position_after_both_puts_it_last(self, run_context: dict) -> None:
        await insert_third_section(run_context["session"], position=Decimal(300))
        report = await run_to_report(**_run_args(run_context))
        assert report.content["sections"][-1] == THIRD_KEY


class TestDeclaredOrderSurvivesTheDatabase:
    """The author's field order is part of the contract, so storage must not reorder it.

    It did once. ``jsonb`` normalises keys by length and then bytewise, so a contract
    declaring ``thesis, key_points, key_risks`` came back as ``thesis, key_risks,
    key_points`` and a figures table declaring ``label, value, unit`` rendered its columns
    as ``Unit, Label, Value``. Migration 0007 moved the column to ``json``; these tests
    are what stop it moving back.
    """

    async def test_the_seeded_contract_keeps_its_declared_order(
        self, db_session: AsyncSession
    ) -> None:
        definition = await db_session.scalar(
            select(SectionDefinition).where(SectionDefinition.key == "executive_summary")
        )
        assert definition is not None
        assert list(definition.output_contract["properties"]) == [
            "thesis",
            "key_points",
            "key_risks",
        ]

    async def test_a_contract_written_now_reads_back_in_the_same_order(
        self, db_session: AsyncSession
    ) -> None:
        """Deliberately ordered so that every normalising scheme would produce something
        else: by key length it is c, bb, aaa; alphabetically it is aaa, bb, c."""
        definition = SectionDefinition(
            key="order_probe",
            version=1,
            origin="builtin",
            title="Order Probe",
            position=Decimal(900),
            required=False,
            output_contract={
                "type": "object",
                "properties": {
                    "aaa": {"type": "string"},
                    "c": {"type": "string"},
                    "bb": {"type": "string"},
                },
            },
            evidence_policy={},
            token_budget=100,
            allowed_tools=[],
            applicability={},
        )
        db_session.add(definition)
        await db_session.commit()
        db_session.expunge(definition)

        reloaded = await db_session.scalar(
            select(SectionDefinition).where(SectionDefinition.key == "order_probe")
        )
        assert reloaded is not None
        assert list(reloaded.output_contract["properties"]) == ["aaa", "c", "bb"]

    async def test_the_rendered_headings_follow_the_declared_order(self, run_context: dict) -> None:
        report = await run_to_report(**_run_args(run_context))
        markdown = report.content["markdown"]

        assert markdown.index("### Thesis") < markdown.index("### Key Points")
        assert markdown.index("### Key Points") < markdown.index("### Key Risks")

    async def test_a_tables_columns_come_from_the_contract(self, run_context: dict) -> None:
        """``label, value, unit`` as declared — not ``unit, label, value`` by key length."""
        report = await run_to_report(**_run_args(run_context))
        header = next(
            line for line in report.content["markdown"].splitlines() if line.startswith("| Label")
        )
        assert header == "| Label | Value | Unit |"


class TestCitationsResolve:
    """A footnote that points at nothing is worse than no footnote."""

    @pytest.fixture
    async def report(self, run_context: dict) -> Report:
        return await run_to_report(**_run_args(run_context))

    async def test_no_footnote_is_unresolved(self, report: Report) -> None:
        assert "Unresolved citation" not in report.content["markdown"]

    async def test_a_source_footnote_carries_the_url_and_the_retrieval_date(
        self, report: Report
    ) -> None:
        markdown = report.content["markdown"]
        assert "<https://data.sec.gov/api/xbrl/companyfacts/" in markdown
        assert "retrieved " in markdown

    async def test_a_calculation_footnote_carries_the_formula_and_code_version(
        self, report: Report
    ) -> None:
        markdown = report.content["markdown"]
        assert "Calculated: `" in markdown
        assert "code version `" in markdown

    async def test_the_appendix_hash_matches_the_stored_artefact(
        self, report: Report, run_context: dict
    ) -> None:
        """The reader can take the digest, find the bytes and confirm they are the bytes."""
        session: AsyncSession = run_context["session"]
        store: LocalArtefactStore = run_context["store"]

        document = await session.scalar(select(SourceDocument))
        assert document is not None
        artefact = await session.get(Artefact, document.artefact_id)
        assert artefact is not None

        # The prefix printed in the appendix.
        assert artefact.sha256[:12] in report.content["markdown"]
        # And the bytes really are those bytes: verify recomputes the digest from disk.
        assert await store.verify(artefact.sha256) == artefact.size_bytes


def executable_source(path: Path) -> str:
    """A module's code with comments and docstrings removed, and nothing else.

    The invariant is that no code *acts on* a section key. A comment saying
    ``'executive_summary', or 'custom.moat_durability' for a user-defined one`` documents a
    column's format and constrains nothing; flagging it would make this a style rule about
    prose rather than a check on behaviour.

    **Ordinary string literals are kept.** A hardcoded section list would be written as
    strings, so stripping every string would leave the check unable to see the thing it
    exists to find. Round-tripping through :func:`ast.unparse` drops comments for free and
    keeps every literal that is not a docstring.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                node.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree)


class TestNoSectionKeyIsHardcoded:
    """No code outside the seed migration may name a section."""

    @staticmethod
    def _code_mentioning(key: str) -> set[Path]:
        return {path for path in SRC_ROOT.rglob("*.py") if key in executable_source(path)}

    @pytest.mark.parametrize("key", SEEDED_KEYS)
    def test_no_source_file_names_a_seeded_section(self, key: str) -> None:
        offenders = self._code_mentioning(key)
        assert offenders == set(), (
            f"{key!r} appears in the code of {sorted(str(p) for p in offenders)}. Sections "
            "are rows; a module that names one has made the next section a code change."
        )

    def test_the_seed_migration_does_name_them(self) -> None:
        """Guards the test above from passing because the keys were renamed everywhere."""
        text = SEED_MIGRATION.read_text(encoding="utf-8")
        for key in SEEDED_KEYS:
            assert key in text

    def test_the_scan_sees_a_hardcoded_key_and_ignores_a_documented_one(
        self, tmp_path: Path
    ) -> None:
        """Guards the scan itself, in both directions.

        A stripper that removed too much would pass silently while a section list sat in
        the renderer; one that removed too little would fail on a comment. Both forms are
        checked here rather than assumed.
        """
        key = SEEDED_KEYS[0]

        offending = tmp_path / "offender.py"
        offending.write_text(
            f'ORDER = ["{key}"]\n\n\ndef render(section):\n    return section.key in ORDER\n',
            encoding="utf-8",
        )
        assert key in executable_source(offending)

        documented = tmp_path / "documented.py"
        documented.write_text(
            f'"""A key looks like {key}."""\n\n# For example {key}.\nVALUE = 1\n',
            encoding="utf-8",
        )
        assert key not in executable_source(documented)

    def test_there_is_no_section_enum(self) -> None:
        """An enum of sections is the same mistake as a list of them, spelled differently."""
        enums = (SRC_ROOT / "aer" / "core" / "enums.py").read_text(encoding="utf-8")
        assert "SectionKey" not in enums
        for key in SEEDED_KEYS:
            assert key not in enums


def _run_args(context: dict) -> dict:
    return {
        "session": context["session"],
        "settings": context["settings"],
        "provider": context["provider"],
        "store": context["store"],
        "sec_client": context["sec_client"],
        "job": context["job"],
        "user": context["user"],
    }
