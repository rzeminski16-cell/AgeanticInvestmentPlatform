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
import os
import re
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.config import HouseStyle, Settings
from aer.core.enums import Decision, GateKind
from aer.db.models import Artefact, Job, JobStep, Report, SectionDefinition
from aer.providers.fake import FakeProvider
from aer.render import display
from aer.services import approvals as approval_service
from aer.services import runs as run_service
from aer.storage.local import LocalArtefactStore
from tests.workflow_fixtures import (
    SPINE_KEYS,
    StubSecClient,
    gate_for,
    paused_at,
    seed_job,
    seed_request,
    seed_user,
)

pytestmark = pytest.mark.anyio

SRC_ROOT = Path(__file__).resolve().parent.parent / "src"
_VERSIONS = Path(__file__).resolve().parent.parent / "migrations" / "versions"
SEED_MIGRATIONS = (
    _VERSIONS / "0006_agents_costs_prompts_sections.py",
    _VERSIONS / "0023_the_eighteen_section_spine.py",
)

# The eighteen-section spine, in position order — what the seed migrations insert. The
# source scan below looks for these keys and the full-run tests assert the order.
SEEDED_KEYS = SPINE_KEYS

# The two platform-filled sections. Their builders live in `aer/sections/deterministic.py`,
# which is therefore the one source module allowed to name them — it is the seed's
# counterpart: the row says "code fills me" and the registry is where that code is bound.
DETERMINISTIC_KEYS = ("prior_research_comparison", "validation_disagreements")
# Sections whose *platform-filled fields* are bound in the registry (ADR 0063). The same
# seed-counterpart standing as the deterministic keys: the row's contract marks fields
# code must fill, and the registry is where that code attaches.
AUGMENTED_KEYS = ("valuation_dcf",)
DETERMINISTIC_REGISTRY = SRC_ROOT / "aer" / "sections" / "deterministic.py"

# What the inserted section is called. Deliberately nothing like any built-in, so a
# renderer that happened to handle those by name could not accidentally handle this.
THIRD_KEY = "supply_chain_resilience"
THIRD_TITLE = "Supply Chain Resilience"

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
    """Drive a whole run, approving every gate on the way, and return its report.

    The FINAL hash is sealed by the red_team step since task 40 — the last step that can
    change what the operator is shown. Everything before it is cleared by **asking the run
    which gate it stopped at** rather than from a list: the peer set (ADR 0059) and the
    assumptions (gap S2) are both conditional, and a fixed sequence goes wrong the moment
    one of them starts firing where it used to pass through.
    """

    async def advance() -> None:
        await run_service.execute(
            session,
            job=job,
            settings=settings,
            provider=provider,
            store=store,
            sec_client=sec_client,
        )

    async def approve(gate: GateKind, step: str) -> None:
        row = await session.scalar(
            select(JobStep).where(JobStep.job_id == job.id, JobStep.step_key == step)
        )
        assert row is not None, f"the {step} step has not run"
        await approval_service.record_decision(
            session,
            job=job,
            gate=gate,
            decision=Decision.APPROVED,
            actor=user,
            payload_hash=str((row.output_ref or {})["payload_hash"]),
        )

    await advance()
    await approve(GateKind.PLAN, "plan")
    await advance()

    while (clearing := gate_for(await paused_at(session, job.id))) is not None:
        await approve(*clearing)
        await advance()

    await approve(GateKind.FINAL, "red_team")
    await advance()

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
    """What the seeded spine produces, so an inserted section has something to change."""

    @pytest.fixture
    async def report(self, run_context: dict) -> Report:
        return await run_to_report(**_run_args(run_context))

    async def test_it_has_the_whole_spine_in_position_order(self, report: Report) -> None:
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
        # 155: between the seeded 150 and 200. The sparse numbering is the whole reason a
        # section can be slotted in without renumbering anything.
        await insert_third_section(run_context["session"], position=Decimal(155))
        return await run_to_report(**_run_args(run_context))

    async def test_the_report_gains_the_section(self, report: Report) -> None:
        assert THIRD_KEY in report.content["sections"]

    async def test_it_lands_between_its_positional_neighbours(self, report: Report) -> None:
        found = report.content["sections"]
        assert found.index("management_governance") + 1 == found.index(THIRD_KEY)
        assert found.index(THIRD_KEY) + 1 == found.index("historical_financial_analysis")

    async def test_its_heading_appears_in_the_right_place(self, report: Report) -> None:
        found = headings(report.content["markdown"])
        assert found.index("Management & Governance") < found.index(THIRD_TITLE)
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

    async def test_a_position_after_the_whole_spine_puts_it_last(self, run_context: dict) -> None:
        await insert_third_section(run_context["session"], position=Decimal(950))
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
        # The latest version: migration 0023 published v2, appending a figures table to
        # the 0006 contract without disturbing the declared order of the original fields.
        definition = await db_session.scalar(
            select(SectionDefinition)
            .where(SectionDefinition.key == "executive_summary")
            .order_by(SectionDefinition.version.desc())
        )
        assert definition is not None
        assert list(definition.output_contract["properties"]) == [
            "thesis",
            "key_points",
            "key_risks",
            "headline_figures",
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
        """``label`` before ``value`` as declared — not reordered by key length. The
        ``unit`` column the contract also declares is absent deliberately: the display
        formatter folds each row's unit into its value (gap R1), and a "Unit: USD" column
        beside "$391,035m" would be the machine's bookkeeping shown to a reader."""
        report = await run_to_report(**_run_args(run_context))
        # Presence, not first match: the at-a-glance block (gap R10) leads the document
        # with its own three-column table, and the section tables keep this shape.
        assert "| Label | Value |" in report.content["markdown"].splitlines()


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
        """The reader can take a printed digest, find the bytes and confirm they are
        the bytes.

        Starts from what the appendix *printed*, not from a stored row, because the two
        are not the same set: the appendix lists the documents the report cites, and a
        run legitimately acquires documents it never cites. The previous version picked
        one of the job's documents with an unordered `scalar()` — so which one it checked
        shifted with unrelated tests' writes, and it failed only in the full suite,
        whenever the arbitrary pick landed on an acquired-but-uncited row.

        Confined to the Sources table, because calculation footnotes print a code-version
        prefix of the same twelve-hex-character shape and a digest check must not be
        asked to verify a git commit.
        """
        session: AsyncSession = run_context["session"]
        store: LocalArtefactStore = run_context["store"]

        _, sources = report.content["markdown"].split("## Sources", 1)
        printed = set(re.findall(r"`([0-9a-f]{12})`", sources))
        assert printed, "the Sources table printed no digest at all"

        artefacts = list(await session.scalars(select(Artefact)))
        by_prefix = {artefact.sha256[:12]: artefact for artefact in artefacts}
        for prefix in printed:
            artefact = by_prefix.get(prefix)
            assert artefact is not None, f"digest {prefix} resolves to no stored artefact"
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


FULL_GOLDEN = Path(__file__).resolve().parent / "fixtures" / "full_run" / "golden.md"

# What two runs of the same offline workflow legitimately disagree on: generated ids,
# the render timestamp, the retrieval clock, and the code version each calculation
# footnote prints (the working tree's own git sha). Everything else is the fixture's.
_UUID_TOKEN = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.IGNORECASE
)
_GENERATED_LINE = re.compile(r"\*\*Generated:\*\* .+")
_CODE_VERSION = re.compile(r"code version `[0-9a-f]{6,40}`")


def _normalised(markdown: str) -> str:
    text = _UUID_TOKEN.sub("<id>", markdown)
    text = _GENERATED_LINE.sub("**Generated:** <generated>", text)
    text = _CODE_VERSION.sub("code version `<code>`", text)
    today = display.date_text(datetime.now(UTC).date(), style=HouseStyle())
    return text.replace(today, "<today>")


class TestTheGoldenFullRun:
    """Gap O6: the whole offline run, rendered and held byte for byte.

    The ``fx_report`` goldens pin a small fixture; this one pins what the workflow
    actually produces end to end — spine order, the at-a-glance block, interleaved
    footnote numbering, the notes and the sources — after the tokens two honest runs
    legitimately disagree on (ids, clocks) are normalised away. The merged footnote
    markers and the contents' missing space would each have failed here before a live
    PDF showed them.
    """

    async def test_the_rendered_run_matches_the_recorded_golden(self, run_context: dict) -> None:
        report = await run_to_report(**_run_args(run_context))
        markdown = _normalised(report.content["markdown"])

        if os.environ.get("UPDATE_GOLDEN"):
            FULL_GOLDEN.parent.mkdir(parents=True, exist_ok=True)
            FULL_GOLDEN.write_text(markdown, encoding="utf-8")
            pytest.fail("full-run golden re-recorded; rerun without UPDATE_GOLDEN")

        assert FULL_GOLDEN.exists(), "record the golden with UPDATE_GOLDEN=1"
        assert markdown == FULL_GOLDEN.read_text(encoding="utf-8")


class TestNoSectionKeyIsHardcoded:
    """No code outside the seed migration may name a section."""

    @staticmethod
    def _code_mentioning(key: str) -> set[Path]:
        return {path for path in SRC_ROOT.rglob("*.py") if key in executable_source(path)}

    @pytest.mark.parametrize("key", SEEDED_KEYS)
    def test_no_source_file_names_a_seeded_section(self, key: str) -> None:
        """One scoped exception: the deterministic registry may name the keys it binds.

        A ``token_budget = 0`` row says "code fills me", and a ``platform_filled`` field
        on a contract says the same of that field (ADR 0063); the registry in
        `aer/sections/deterministic.py` is where that code is bound — it is the seed's
        counterpart, not a leak. Every other module is held to the rule for every key,
        and the registry itself is held to it for the purely model-written keys.
        """
        allowed = (
            {DETERMINISTIC_REGISTRY} if key in (*DETERMINISTIC_KEYS, *AUGMENTED_KEYS) else set()
        )
        offenders = self._code_mentioning(key) - allowed
        assert offenders == set(), (
            f"{key!r} appears in the code of {sorted(str(p) for p in offenders)}. Sections "
            "are rows; a module that names one has made the next section a code change."
        )

    def test_the_deterministic_registry_does_bind_its_keys(self) -> None:
        """Guards the exception above from outliving a rename of the registry."""
        text = executable_source(DETERMINISTIC_REGISTRY)
        for key in (*DETERMINISTIC_KEYS, *AUGMENTED_KEYS):
            assert key in text

    def test_the_seed_migrations_do_name_them(self) -> None:
        """Guards the scan from passing because the keys were renamed everywhere."""
        text = "".join(path.read_text(encoding="utf-8") for path in SEED_MIGRATIONS)
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
