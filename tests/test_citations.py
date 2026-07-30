"""The strongest control in the platform: a citation is confirmed by code or not at all.

Invariant 2 and threat T10. The model may *propose* a citation; only
:func:`aer.verify.citations.verify` may confirm one, and it does so by re-reading the artefact
by hash and finding the excerpt at the recorded locator.

**Three of these are the tests that matter**, and they are the ones a plausible-looking
implementation fails:

* A fabricated excerpt is rejected — the hallucinated-citation rate §2.10 holds at zero.
* An excerpt that exists *elsewhere in the same document* is rejected at the wrong locator. A
  verifier that searched the text instead of slicing it would pass this, and would go on to
  accept citations pointing at the wrong paragraph, the wrong year, or another segment's
  figures. The fixture below contains the same sentence with a different year in it for
  exactly this reason.
* ``excerpt_verified`` cannot be written from anywhere but the verifier, proved by reading the
  source tree rather than by asking nicely.
"""

from __future__ import annotations

import ast
import inspect
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError as DbIntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from aer.config import Settings
from aer.core.enums import ClaimKind, JobStatus, Provider, SourceTier
from aer.db.models import (
    Artefact,
    Citation,
    Claim,
    Job,
    ReportSection,
    ResearchRequest,
    SectionDefinition,
    SectionStatus,
    SourceDocument,
    User,
)
from aer.errors import ConflictError, ValidationError
from aer.extract.html import extract_html
from aer.services.citations import (
    override_citation,
    record_citation,
    record_claim,
    review_evidence,
)
from aer.services.extractions import record_excerpt
from aer.storage.local import LocalArtefactStore
from aer.verify.citations import MATCH_THRESHOLD, VERIFICATION_METHOD, ReadOnce, verify
from tests.workflow_fixtures import AS_OF_DATE

SRC_ROOT = Path(__file__).resolve().parent.parent / "src"

# The one module permitted to write the verification flag.
VERIFIER = SRC_ROOT / "aer" / "verify" / "citations.py"
# The model declares the column. A declaration is not a write.
MODEL = SRC_ROOT / "aer" / "db" / "models" / "citation.py"

FILING = b"""<!DOCTYPE html><html><head><title>10-K</title></head><body>
<p>Total revenue was $198,270 million for fiscal year 2022.</p>
<p>Operating income was $83,383 million for fiscal year 2022.</p>
<p>Total revenue was $168,088 million for fiscal year 2021.</p>
</body></html>"""

CITED = "Total revenue was $198,270 million for fiscal year 2022."
ANOTHER_YEAR = "Total revenue was $168,088 million for fiscal year 2021."


# -- The structural guarantee ----------------------------------------------------------------


class TestOnlyTheVerifierConfirmsACitation:
    """A rule enforced by convention lasts until somebody is in a hurry."""

    @staticmethod
    def _files_writing_the_flag() -> set[Path]:
        """Every source file that assigns to ``excerpt_verified``.

        Parsed rather than grepped, so the name appearing in a docstring, a query filter or a
        log line does not count — only an assignment does, whether by attribute or by keyword
        argument.
        """
        offenders: set[Path] = set()
        for path in SRC_ROOT.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if _assigns_the_flag(node) or _passes_the_flag(node):
                    offenders.add(path)
        return offenders

    def test_no_module_but_the_verifier_sets_it(self) -> None:
        assert self._files_writing_the_flag() <= {VERIFIER, MODEL}

    def test_the_verifier_really_does_set_it(self) -> None:
        """Guards the test above from passing because nothing sets the flag at all."""
        assert VERIFIER in self._files_writing_the_flag()

    def test_the_service_layer_cannot_confirm_a_citation(self) -> None:
        """``record_citation`` has no argument that could make one verified, and the absence
        is the control: a caller acting on a model's suggestion can propose, not confirm."""
        parameters = set(inspect.signature(record_citation).parameters)

        assert not parameters & {"excerpt_verified", "verified", "verification_method"}


def _assigns_the_flag(node: ast.AST) -> bool:
    if not isinstance(node, ast.Assign | ast.AugAssign | ast.AnnAssign):
        return False
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    return any(isinstance(t, ast.Attribute) and t.attr == "excerpt_verified" for t in targets)


def _passes_the_flag(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and any(
        keyword.arg == "excerpt_verified" for keyword in node.keywords
    )


# -- Fixtures ---------------------------------------------------------------------------------


@pytest.fixture
def settings(tmp_path: Any) -> Settings:
    return Settings(
        http_user_agent="Ageiantic Test test@example.invalid",
        artefact_root=tmp_path / "artefacts",
    )


@pytest.fixture
def store(settings: Settings) -> LocalArtefactStore:
    return LocalArtefactStore(settings.artefact_root, max_bytes=settings.max_artefact_bytes)


@pytest.fixture
async def scene(db_session: AsyncSession, store: LocalArtefactStore) -> dict[str, Any]:
    """A run with one drafted section, one archived filing, and one extracted excerpt."""
    user = User(email="cite@example.invalid", display_name="Cite")
    db_session.add(user)
    await db_session.flush()

    request = ResearchRequest(
        user_id=user.id,
        company_name="Microsoft Corporation",
        ticker="MSFT",
        exchange="NASDAQ",
        as_of_date=AS_OF_DATE,
        point_in_time=True,
        base_currency="USD",
        reporting_currency="USD",
        investment_horizon_months=12,
        max_cost_gbp="2.50",
    )
    db_session.add(request)
    await db_session.flush()

    job = Job(
        request_id=request.id,
        workflow_version="vertical_slice_v1",
        code_version="test",
        status=JobStatus.RUNNING,
        started_at=datetime.now(UTC),
    )
    db_session.add(job)
    await db_session.flush()

    definition = await db_session.scalar(select(SectionDefinition).limit(1))
    assert definition is not None, "the migration seeds section definitions"

    section = ReportSection(
        job_id=job.id,
        section_definition_id=definition.id,
        section_key=definition.key,
        position=definition.position,
        status=SectionStatus.GENERATED,
        content={"body": "Revenue grew."},
    )
    db_session.add(section)
    await db_session.flush()

    stored = await store.put_bytes(FILING)
    artefact = Artefact(
        sha256=stored.sha256,
        media_type="text/html",
        size_bytes=stored.size_bytes,
        storage_key=store.storage_key_for(stored.sha256),
    )
    db_session.add(artefact)
    await db_session.flush()

    document = SourceDocument(
        request_id=request.id,
        job_id=job.id,
        artefact_id=artefact.id,
        url="https://www.sec.gov/Archives/edgar/data/789019/msft-10k.htm",
        provider=Provider.SEC_EDGAR,
        source_tier=SourceTier.T1_REGULATORY,
        retrieved_at=datetime.now(UTC),
        quarantined=False,
    )
    db_session.add(document)
    await db_session.flush()

    extracted = extract_html(FILING).text
    excerpt = extracted.locate(CITED)
    assert excerpt is not None

    extraction = await record_excerpt(
        db_session,
        source_document_id=document.id,
        extracted=extracted,
        excerpt=excerpt,
    )

    return {
        "user": user,
        "job": job,
        "section": section,
        "document": document,
        "artefact": artefact,
        "extraction": extraction,
        "extracted": extracted,
        "store": store,
    }


async def _cited_claim(
    session: AsyncSession, scene: dict[str, Any], *, kind: ClaimKind = ClaimKind.FACTUAL
) -> Citation:
    claim = await record_claim(
        session, section=scene["section"], kind=kind, text="Revenue grew year on year."
    )
    return await record_citation(
        session,
        claim=claim,
        source_document_id=scene["document"].id,
        extraction_id=scene["extraction"].id,
    )


# -- The verifier -------------------------------------------------------------------------


@pytest.mark.integration
class TestVerifyingAnExcerpt:
    async def test_a_real_excerpt_verifies(
        self, db_session: AsyncSession, scene: dict[str, Any], settings: Settings
    ) -> None:
        citation = await _cited_claim(db_session, scene)

        outcome = await verify(db_session, scene["store"], citation=citation, settings=settings)

        assert outcome.verified
        assert outcome.ratio == Decimal("1.000")
        assert citation.excerpt_verified is True
        assert citation.verification_method == VERIFICATION_METHOD
        assert citation.verified_at is not None

    async def test_a_fabricated_excerpt_is_rejected(
        self, db_session: AsyncSession, scene: dict[str, Any], settings: Settings
    ) -> None:
        """The hallucinated-citation rate, which §2.10 holds at zero.

        The excerpt is plausible, well formed, and about the right company — and it is not in
        the document. Nothing but re-reading the document can tell the difference.
        """
        citation = await _cited_claim(db_session, scene)
        scene["extraction"].excerpt = "Total revenue was $250,000 million for fiscal year 2022."
        await db_session.flush()

        outcome = await verify(db_session, scene["store"], citation=citation, settings=settings)

        assert outcome.failed
        assert citation.excerpt_verified is False
        assert "does not match" in (outcome.reason or "")

    async def test_an_excerpt_from_elsewhere_in_the_document_is_rejected(
        self, db_session: AsyncSession, scene: dict[str, Any], settings: Settings
    ) -> None:
        """**The test a text search would fail.**

        This sentence really is in the filing — one paragraph down, about a different year.
        A verifier that looked for the excerpt anywhere in the document would confirm it, and
        would thereafter accept a citation pointing at the wrong year's figures. Slicing at the
        locator is what makes the difference, and this is the assertion that proves the
        implementation slices.
        """
        citation = await _cited_claim(db_session, scene)
        scene["extraction"].excerpt = ANOTHER_YEAR
        await db_session.flush()

        outcome = await verify(db_session, scene["store"], citation=citation, settings=settings)

        assert outcome.failed
        assert ANOTHER_YEAR in scene["extracted"].text, (
            "the fixture no longer contains this sentence elsewhere, so the test proves nothing"
        )

    async def test_whitespace_differences_do_not_fail_a_good_citation(
        self, db_session: AsyncSession, scene: dict[str, Any], settings: Settings
    ) -> None:
        """A document reflowed by a parser update is the same document. Failing its citations
        would be a false alarm, and false alarms are how a control gets switched off."""
        citation = await _cited_claim(db_session, scene)
        scene["extraction"].excerpt = CITED.replace(" ", "\n  ")
        await db_session.flush()

        outcome = await verify(db_session, scene["store"], citation=citation, settings=settings)

        assert outcome.verified

    async def test_the_ratio_is_recorded_on_a_failure_too(
        self, db_session: AsyncSession, scene: dict[str, Any], settings: Settings
    ) -> None:
        """The difference between 0.94 and 0.02 is the difference between a reflowed paragraph
        and a fabrication, and an operator deciding whether to override needs to see which."""
        citation = await _cited_claim(db_session, scene)
        scene["extraction"].excerpt = "Something else entirely."
        await db_session.flush()

        await verify(db_session, scene["store"], citation=citation, settings=settings)

        assert citation.match_ratio is not None
        assert citation.match_ratio < MATCH_THRESHOLD

    async def test_a_tampered_artefact_fails_before_the_excerpt_is_considered(
        self, db_session: AsyncSession, scene: dict[str, Any], settings: Settings
    ) -> None:
        """Threat T8. The store verifies the digest as it reads, so the failure is "this is not
        the document it claims to be" rather than "this quote is wrong" — which would send an
        operator to re-read a filing that is no longer the filing.

        The store refuses the read outright, so the tampered bytes never reach the comparison
        at all — which is the behaviour the threat model promises and, until this test was
        written, was not what `read` actually did.
        """
        citation = await _cited_claim(db_session, scene)
        path = settings.artefact_root / scene["artefact"].storage_key
        path.write_bytes(FILING.replace(b"198,270", b"999,999"))

        outcome = await verify(db_session, scene["store"], citation=citation, settings=settings)

        assert outcome.failed
        assert "could not be re-read" in (outcome.reason or "")
        assert "bytes have changed" in (outcome.reason or "")

    async def test_an_extractor_that_has_changed_says_so(
        self, db_session: AsyncSession, scene: dict[str, Any], settings: Settings
    ) -> None:
        """ "The tool changed" and "the quote is wrong" look identical from the mismatch alone,
        and need different responses: re-extract the document, or go and look at the claim."""
        citation = await _cited_claim(db_session, scene)
        scene["extraction"].content_hash = "0" * 64
        await db_session.flush()

        outcome = await verify(db_session, scene["store"], citation=citation, settings=settings)

        assert outcome.failed
        assert "Re-extract the document" in (outcome.reason or "")

    async def test_a_document_is_re_extracted_once_per_pass_not_once_per_citation(
        self, db_session: AsyncSession, scene: dict[str, Any], settings: Settings
    ) -> None:
        """A gate check that spawned a parse per citation would take a minute over forty of
        them, and a slow gate is a gate somebody finds a way around."""
        reads: list[str] = []
        original = scene["store"].read

        async def counting(sha256: str) -> bytes:
            reads.append(sha256)
            return await original(sha256)

        scene["store"].read = counting  # type: ignore[method-assign]
        documents = ReadOnce(scene["store"], settings)

        for _ in range(3):
            citation = await _cited_claim(db_session, scene)
            await verify(
                db_session,
                scene["store"],
                citation=citation,
                settings=settings,
                documents=documents,
            )

        assert len(reads) == 1

    async def test_re_verifying_withdraws_an_earlier_pass(
        self, db_session: AsyncSession, scene: dict[str, Any], settings: Settings
    ) -> None:
        """A verifier that only ever set the flag would leave a stale ``true`` standing behind
        a report whose evidence had moved."""
        citation = await _cited_claim(db_session, scene)
        await verify(db_session, scene["store"], citation=citation, settings=settings)
        assert citation.excerpt_verified is True

        scene["extraction"].excerpt = "Nothing like the document."
        await db_session.flush()
        await verify(db_session, scene["store"], citation=citation, settings=settings)

        assert citation.excerpt_verified is False
        assert citation.verified_at is None
        assert citation.verification_method is None


# -- What a gate makes of the verdicts --------------------------------------------------------


@pytest.mark.integration
class TestReviewingTheEvidence:
    async def test_an_unverified_citation_leaves_its_claim_unsupported(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        await _cited_claim(db_session, scene)

        review = await review_evidence(db_session, job_id=scene["job"].id)

        assert not review.is_admissible
        assert len(review.unsupported) == 1
        assert len(review.unverified) == 1

    async def test_a_verified_citation_supports_its_claim(
        self, db_session: AsyncSession, scene: dict[str, Any], settings: Settings
    ) -> None:
        citation = await _cited_claim(db_session, scene)
        await verify(db_session, scene["store"], citation=citation, settings=settings)

        review = await review_evidence(db_session, job_id=scene["job"].id)

        assert review.is_admissible
        assert review.verified == 1

    async def test_a_claim_with_no_citation_at_all_is_unsupported(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        await record_claim(
            db_session,
            section=scene["section"],
            kind=ClaimKind.FACTUAL,
            text="Microsoft's fiscal year ends on 30 June.",
        )

        review = await review_evidence(db_session, job_id=scene["job"].id)

        assert len(review.unsupported) == 1

    async def test_an_opinion_needs_no_citation(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        """§2.9 sets a different bar for each kind. An opinion with a citation attached is not
        better supported than one without, and holding it to the numeric rule would push a
        writer to attach evidence that does not bear on it."""
        await record_claim(
            db_session,
            section=scene["section"],
            kind=ClaimKind.OPINION,
            text="The competitive position looks durable.",
        )

        review = await review_evidence(db_session, job_id=scene["job"].id)

        assert review.is_admissible

    async def test_the_message_names_what_is_wrong(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        await _cited_claim(db_session, scene)

        message = (await review_evidence(db_session, job_id=scene["job"].id)).as_message()

        assert "did not verify" in message
        assert "no admissible citation" in message


# -- Overriding ---------------------------------------------------------------------------------


@pytest.mark.integration
class TestOverriding:
    async def test_an_override_admits_the_citation_without_verifying_it(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        """Both facts survive: the check failed, and a named person accepted it anyway.
        Collapsing them into one boolean would let an override read as a verification."""
        citation = await _cited_claim(db_session, scene)

        await override_citation(
            db_session,
            citation=citation,
            actor=scene["user"],
            reason="The filing's HTML is malformed; I checked the PDF by hand.",
        )

        assert citation.excerpt_verified is False
        assert citation.is_admissible is True
        review = await review_evidence(db_session, job_id=scene["job"].id)
        assert review.is_admissible
        assert review.overridden == 1
        assert review.verified == 0

    async def test_an_override_records_who_and_when(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        citation = await _cited_claim(db_session, scene)

        await override_citation(
            db_session, citation=citation, actor=scene["user"], reason="Checked by hand."
        )

        assert citation.overridden_by_user_id == scene["user"].id
        assert citation.overridden_at is not None

    async def test_an_override_needs_a_reason(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        """Without one it records a click, not a decision."""
        citation = await _cited_claim(db_session, scene)

        with pytest.raises(ValidationError, match="written reason"):
            await override_citation(
                db_session, citation=citation, actor=scene["user"], reason="   "
            )

    async def test_a_verified_citation_cannot_be_overridden(
        self, db_session: AsyncSession, scene: dict[str, Any], settings: Settings
    ) -> None:
        """Recording a reason against evidence that checked out would imply a doubt the
        evidence does not support."""
        citation = await _cited_claim(db_session, scene)
        await verify(db_session, scene["store"], citation=citation, settings=settings)

        with pytest.raises(ConflictError, match="nothing to override"):
            await override_citation(
                db_session, citation=citation, actor=scene["user"], reason="Just in case."
            )

    async def test_overrides_are_one_at_a_time(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        """There is no bulk form, and the absence is the design: a reviewer waving through
        twelve unverified citations in one click has not reviewed twelve citations."""
        parameters = set(inspect.signature(override_citation).parameters)

        assert "citations" not in parameters
        assert "citation" in parameters


# -- What a claim may assert -----------------------------------------------------------------


@pytest.mark.integration
class TestWhatAClaimMayAssert:
    async def test_a_numeric_claim_must_name_its_figure(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        """Invariant 3: no number reaches a report unless something computed or reported it."""
        with pytest.raises(ValidationError, match="must name exactly one figure"):
            await record_claim(
                db_session,
                section=scene["section"],
                kind=ClaimKind.NUMERIC,
                text="Revenue was $198,270 million.",
            )

    async def test_an_opinion_must_not_name_a_figure(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        """A figure id on a statement nothing checks would make it look verified downstream."""
        with pytest.raises(ValidationError, match="must not name a figure"):
            await record_claim(
                db_session,
                section=scene["section"],
                kind=ClaimKind.OPINION,
                text="Margins look defensible.",
                calculation_id=scene["job"].id,
            )

    async def test_the_database_refuses_it_too(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        """The service check is for the message; the constraint is for everyone who writes an
        INSERT without reading the service."""
        db_session.add(
            Claim(
                report_section_id=scene["section"].id,
                kind=ClaimKind.NUMERIC,
                text="Revenue was $198,270 million.",
            )
        )

        with pytest.raises(DbIntegrityError, match="ck_claims_numeric_claims_name_one_figure"):
            await db_session.flush()


# -- What cannot be deleted --------------------------------------------------------------------


@pytest.mark.integration
class TestEvidenceOutlivesTidyingUp:
    async def test_an_extraction_a_citation_rests_on_cannot_be_deleted(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        """The protection ADR 0017 promised when it made extractions cascade from source
        documents: the cascade stops at evidence a published claim rests on."""
        await _cited_claim(db_session, scene)

        await db_session.delete(scene["extraction"])

        with pytest.raises(DbIntegrityError, match="violates foreign key constraint"):
            await db_session.flush()

    async def test_re_drafting_a_section_takes_its_claims_with_it(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        """Claims cascade from their section. Orphans would be counted by every coverage
        metric and cited by none."""
        await record_claim(
            db_session,
            section=scene["section"],
            kind=ClaimKind.FACTUAL,
            text="Microsoft's fiscal year ends on 30 June.",
        )

        await db_session.delete(scene["section"])
        await db_session.flush()

        assert await db_session.scalar(select(func.count()).select_from(Claim)) == 0
