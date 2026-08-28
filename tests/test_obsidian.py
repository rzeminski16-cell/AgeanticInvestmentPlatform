"""The vault as a projection, and the section 2.8 rules that keep it clean.

The scene extends the citation fixtures' run with an approved report, so the export
tests exercise the same rows a real run leaves behind — including a verified claim whose
block reference and source link the run note must carry.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import frontmatter
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from aer.config import Settings
from aer.core.enums import ClaimKind, Provider, SourceTier
from aer.db.models import Artefact, Company, Report, SourceDocument
from aer.obsidian import (
    SENTINEL,
    ObsidianExportError,
    VaultWriteError,
    VaultWriter,
    export_report,
)
from aer.services.citations import record_citation, record_claim
from aer.storage.local import LocalArtefactStore
from aer.verify.citations import verify
from tests.scene_fixtures import build_scene

pytestmark = [pytest.mark.anyio, pytest.mark.integration]

APPROVED_AT = datetime(2022, 7, 2, 10, 15, tzinfo=UTC)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        http_user_agent="Tracework Test test@example.invalid",
        artefact_root=tmp_path / "artefacts",
        obsidian_vault_root=tmp_path / "vault",
        obsidian_personal_root=tmp_path / "personal",
    )


@pytest.fixture
def store(settings: Settings) -> LocalArtefactStore:
    return LocalArtefactStore(settings.artefact_root, max_bytes=settings.max_artefact_bytes)


@pytest.fixture
async def scene(db_session: AsyncSession, store: LocalArtefactStore) -> dict[str, Any]:
    """The citation scene plus a company, a verified claim and an approved report."""
    built = await build_scene(db_session, store)

    company = Company(name="MICROSOFT CORP", cik="0000789019", ticker="MSFT", exchange="NASDAQ")
    db_session.add(company)
    await db_session.flush()

    claim = await record_claim(
        db_session,
        section=built["section"],
        kind=ClaimKind.FACTUAL,
        text="Revenue grew year on year.",
    )
    citation = await record_citation(
        db_session,
        claim=claim,
        source_document_id=built["document"].id,
        extraction_id=built["extraction"].id,
    )

    unverified = await record_claim(
        db_session,
        section=built["section"],
        kind=ClaimKind.FACTUAL,
        text="A sentence whose citation was never verified.",
    )
    await record_citation(
        db_session,
        claim=unverified,
        source_document_id=built["document"].id,
        extraction_id=built["extraction"].id,
    )

    # Its own bytes: one record per artefact per request (gap C4) — a quarantined twin
    # sharing the scene's artefact is a state the constraint now refuses to represent.
    aggregate_payload = b"<html>the undated aggregate</html>"
    aggregate_artefact = Artefact(
        sha256=hashlib.sha256(aggregate_payload).hexdigest(),
        media_type="text/html",
        size_bytes=len(aggregate_payload),
        storage_key="obsidian/undated-aggregate",
    )
    db_session.add(aggregate_artefact)
    await db_session.flush()
    quarantined = SourceDocument(
        work_order_id=built["request"].id,
        request_id=built["request"].id,
        job_id=built["job"].id,
        artefact_id=aggregate_artefact.id,
        url="https://example.invalid/undated-aggregate",
        provider=Provider.WEB_SEARCH,
        source_tier=SourceTier.T6_UNVERIFIED,
        retrieved_at=datetime.now(UTC),
        quarantined=True,
        quarantine_reason="no determinable publication date",
    )
    db_session.add(quarantined)
    await db_session.flush()

    report = Report(
        job_id=built["job"].id,
        request_id=built["request"].id,
        company_id=company.id,
        as_of_date=built["request"].as_of_date,
        rating="Constructive (non-binding)",
        confidence=0.62,
        valuation_low=Decimal("180"),
        valuation_high=Decimal("220"),
        valuation_currency="USD",
        content={
            "markdown": (
                "# Report\n\n## Prior Research Comparison\n\nA fixed comparison line.\n\n"
                "## Sources\n\ntable\n"
            )
        },
        content_hash="e" * 64,
        approved_at=APPROVED_AT,
        immutable=True,
    )
    db_session.add(report)
    await db_session.flush()

    return {**built, "company": company, "report": report, "claim": claim, "citation": citation}


async def _verify_claim_citation(
    session: AsyncSession, scene: dict[str, Any], settings: Settings
) -> None:
    outcome = await verify(session, scene["store"], citation=scene["citation"], settings=settings)
    assert outcome.verified, outcome.reason


def _vault_files(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*.md"))
    }


class TestTheGuards:
    async def test_a_draft_refuses_to_export(
        self, db_session: AsyncSession, scene: dict[str, Any], settings: Settings
    ) -> None:
        scene["report"].immutable = False
        scene["report"].approved_at = None
        await db_session.flush()

        with pytest.raises(ObsidianExportError, match="never approved"):
            await export_report(db_session, settings=settings, report_id=scene["report"].id)

        # A draft carrying a stray approval timestamp — an approval reverted, or a data
        # fault — still refuses: rule 1 requires both halves, and the permissive reading
        # (export on the strength of either alone) is exactly what the guard refuses.
        # The inverse half-state, immutable with no timestamp, is unrepresentable — the
        # ck_reports_immutable_reports_were_approved check constraint owns that side.
        scene["report"].approved_at = APPROVED_AT
        await db_session.flush()
        with pytest.raises(ObsidianExportError, match="never approved"):
            await export_report(db_session, settings=settings, report_id=scene["report"].id)

        assert not (settings.obsidian_vault_root / "20-Runs").exists()

    async def test_no_vault_configured_refuses_before_touching_anything(
        self, db_session: AsyncSession, scene: dict[str, Any], settings: Settings
    ) -> None:
        bare = settings.model_copy(update={"obsidian_vault_root": None})
        with pytest.raises(ObsidianExportError, match="No Obsidian vault is configured"):
            await export_report(db_session, settings=bare, report_id=scene["report"].id)

    def test_the_writer_refuses_to_leave_the_vault(self, tmp_path: Path) -> None:
        writer = VaultWriter(tmp_path / "vault", personal_root=tmp_path / "personal")

        with pytest.raises(VaultWriteError, match="outside"):
            writer.write("../escape.md", "no")
        with pytest.raises(VaultWriteError, match=r"outside|personal"):
            writer.write("../personal/notes.md", "no")
        with pytest.raises(VaultWriteError, match="reserved"):
            writer.write("99-Personal/mine.md", "no")
        assert not (tmp_path / "escape.md").exists()
        assert not (tmp_path / "personal").exists()

    def test_the_personal_check_holds_even_inside_the_vault(self, tmp_path: Path) -> None:
        """Isolates rule 6 from the outside-vault rule: were someone to configure the
        personal root inside the vault (Settings refuses it, but the writer must not
        depend on that), the personal check itself still refuses the write."""
        writer = VaultWriter(tmp_path / "vault", personal_root=tmp_path / "vault" / "mine")

        with pytest.raises(VaultWriteError, match="personal"):
            writer.write("mine/notes.md", "no")
        assert not (tmp_path / "vault" / "mine").exists()

    async def test_the_personal_directory_is_never_written(
        self, db_session: AsyncSession, scene: dict[str, Any], settings: Settings
    ) -> None:
        personal = settings.obsidian_personal_root
        assert personal is not None
        personal.mkdir(parents=True)
        mine = personal / "my-thoughts.md"
        mine.write_text("private\n", encoding="utf-8")

        await export_report(db_session, settings=settings, report_id=scene["report"].id)

        assert list(personal.iterdir()) == [mine]
        assert mine.read_text(encoding="utf-8") == "private\n"
        assert not (settings.obsidian_vault_root / "99-Personal").exists()


class TestTheExport:
    async def test_an_approved_report_exports_the_tree(
        self, db_session: AsyncSession, scene: dict[str, Any], settings: Settings
    ) -> None:
        await _verify_claim_citation(db_session, scene, settings)
        record = await export_report(db_session, settings=settings, report_id=scene["report"].id)

        vault = settings.obsidian_vault_root
        run_note = vault / "20-Runs" / f"{scene['request'].as_of_date.isoformat()} MSFT.md"
        assert run_note.exists()
        assert (vault / "00-Meta" / "README-generated.md").exists()
        assert (vault / "00-Meta" / "MOC-Companies.md").exists()
        # Exactly the one admissible source: the quarantined one leaves no note at all,
        # because "no draft data in the vault" extends to sources nobody may cite.
        assert len(list((vault / "90-Sources").iterdir())) == 1
        assert any((vault / "10-Companies").iterdir())
        assert len(record.files) == len(list(vault.rglob("*.md")))

    async def test_the_run_note_frontmatter_matches_the_schema(
        self, db_session: AsyncSession, scene: dict[str, Any], settings: Settings
    ) -> None:
        await _verify_claim_citation(db_session, scene, settings)
        await export_report(db_session, settings=settings, report_id=scene["report"].id)

        run_note = (
            settings.obsidian_vault_root
            / "20-Runs"
            / f"{scene['request'].as_of_date.isoformat()} MSFT.md"
        )
        post = frontmatter.loads(run_note.read_text(encoding="utf-8"))

        assert post["aer_kind"] == "run"
        assert post["aer_schema"] == 1
        assert post["report_id"] == str(scene["report"].id)
        assert post["job_id"] == str(scene["job"].id)
        assert post["ticker"] == "MSFT"
        assert post["content_hash"] == "e" * 64
        assert post["rating"] == "Constructive (non-binding)"
        assert post["valuation"]["low"] == "180"
        assert "aer/approved" in post["tags"]
        assert post["evidence_policy"].startswith("derived-from-approved-run")
        # The approval's moment, not the export's: the note has one honest date.
        assert "2022-07-02" in str(post["generated_at"])

    async def test_every_exported_claim_carries_a_block_ref_and_source_link(
        self, db_session: AsyncSession, scene: dict[str, Any], settings: Settings
    ) -> None:
        await _verify_claim_citation(db_session, scene, settings)
        await export_report(db_session, settings=settings, report_id=scene["report"].id)

        run_note = (
            settings.obsidian_vault_root
            / "20-Runs"
            / f"{scene['request'].as_of_date.isoformat()} MSFT.md"
        )
        body = run_note.read_text(encoding="utf-8")
        assert f"^claim-{scene['claim'].id.hex[:12]}" in body
        assert "[[src-" in body
        assert "A fixed comparison line." in body  # the stored comparison, transcribed
        # The unverified claim in the scene never exports: one block reference, not two.
        assert body.count("^claim-") == 1

    async def test_regeneration_preserves_the_personal_half_byte_for_byte(
        self, db_session: AsyncSession, scene: dict[str, Any], settings: Settings
    ) -> None:
        await export_report(db_session, settings=settings, report_id=scene["report"].id)

        company_note = next((settings.obsidian_vault_root / "10-Companies").iterdir())
        first = company_note.read_text(encoding="utf-8")
        assert SENTINEL in first
        personal_half = "\n\n## My thesis journal\n\nHand-written, and mine.\n"
        company_note.write_text(first + personal_half, encoding="utf-8")

        await export_report(db_session, settings=settings, report_id=scene["report"].id)

        regenerated = company_note.read_text(encoding="utf-8")
        assert regenerated.endswith(personal_half)
        assert regenerated.count(SENTINEL) == 1

    async def test_a_second_export_is_idempotent(
        self, db_session: AsyncSession, scene: dict[str, Any], settings: Settings
    ) -> None:
        await _verify_claim_citation(db_session, scene, settings)
        first = await export_report(db_session, settings=settings, report_id=scene["report"].id)
        before = _vault_files(settings.obsidian_vault_root)

        second = await export_report(db_session, settings=settings, report_id=scene["report"].id)
        after = _vault_files(settings.obsidian_vault_root)

        assert before == after
        assert first.files == second.files
        assert first.id != second.id  # the acts are both on record


class TestRuleFour:
    async def test_the_verifier_hard_rejects_a_prior_run_source(
        self, db_session: AsyncSession, scene: dict[str, Any], settings: Settings
    ) -> None:
        """Section 2.8 rule 4: prior research can never support a claim.

        The same citation verifies against the same bytes when the provider is a real
        one, so what this test isolates is the provider rule itself — not admissibility,
        not the excerpt.
        """
        await _verify_claim_citation(db_session, scene, settings)  # passes as sec_edgar

        scene["document"].provider = Provider.INTERNAL_PRIOR_RUN
        await db_session.flush()

        outcome = await verify(
            db_session, scene["store"], citation=scene["citation"], settings=settings
        )
        assert outcome.failed
        assert "internal_prior_run" in (outcome.reason or "")
        assert "cannot support a claim" in (outcome.reason or "")
        assert scene["citation"].excerpt_verified is False
