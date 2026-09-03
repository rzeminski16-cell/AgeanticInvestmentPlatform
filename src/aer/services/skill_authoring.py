"""The authoring surface's read side: validate, preview, diff — nothing written.

Task 43. Three questions an author asks before a skill file is allowed anywhere near a
run, each answered without spending anything and without touching the database:

**"Is this file valid?"** — :func:`validate_skill_source` parses it through the same
:func:`~aer.skills.frontmatter.parse_skill_file` the save path uses, and returns the
line-level issues rather than raising, because an editor showing errors beside the lines
they belong to is the §2.12 requirement and an exception is not a form field.

**"What will it actually run as?"** — the composed-policy preview. The additive-only
composer clamps what a file asked for, and an author who is not shown the clamps will
believe the request. The preview is computed by
:func:`~aer.skills.resolution.compose_for_version`, **the same function plan-time
resolution calls**, over a version row built from the parsed file exactly as the save path
builds it. A preview by a second implementation would eventually disagree with what a run
composes, and the disagreement would surface as a section behaving unlike its preview
with nothing to point at.

**"What would importing this change?"** — threat T20. An imported file is a file somebody
else wrote, so the import path shows a diff against the stored version and refuses to
write without an explicit confirmation carrying the hash of what was displayed. Same
shape as a gate approval, for the same reason: confirming what a page showed is a
different act from confirming whatever the database holds by the time of the click.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncSession

from aer.core.enums import SkillKind
from aer.core.hashing import canonical_json, sha256_hex
from aer.core.skill_guidance import roles_for
from aer.db.models import SkillVersion
from aer.services.skills import current_version
from aer.skills.frontmatter import ParsedSkill, SkillFileError, parse_skill_file
from aer.skills.resolution import (
    compose_for_version,
    contract_schema,
    estimate_custom_section_cost,
)

if TYPE_CHECKING:
    from aer.config import Settings
    from aer.providers.router import Router

__all__ = [
    "ImportDiff",
    "SkillPreview",
    "import_diff",
    "import_payload_hash",
    "validate_skill_source",
]

# How many lines of diff an import confirmation shows. A change nobody can read is a
# change nobody is confirming, and a thousand-line diff is the same as no diff.
DIFF_CONTEXT_LINES = 3


@dataclass(frozen=True, slots=True)
class SkillPreview:
    """What an author is shown before saving: the errors, or the effective policy.

    ``clamps`` is the part that matters. Empty means the file composed unchanged; a
    non-empty list is the platform saying, field by field, that the effective policy
    differs from what was written — the same receipts §2.4's clamp banner shows at gate 2
    when a run executes under one.
    """

    valid: bool
    issues: list[dict[str, Any]] = field(default_factory=list)

    key: str = ""
    kind: str = ""
    title: str = ""
    body: str = ""
    content_hash: str = ""

    # Prompt kinds only (ADR 0108): the roles the skill composes into, from the table
    # that is the last word at composition time. Empty for a custom section.
    composes_into: list[str] = field(default_factory=list)

    # Custom sections only: the composed policy, the receipts, the projected contract and
    # what the section is estimated to cost at its composed budget.
    evidence_policy: dict[str, Any] | None = None
    granted_tools: list[str] = field(default_factory=list)
    token_budget: int | None = None
    clamps: list[dict[str, str]] = field(default_factory=list)
    output_contract: dict[str, Any] | None = None
    estimated_cost_gbp: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "issues": list(self.issues),
            "key": self.key,
            "kind": self.kind,
            "title": self.title,
            "content_hash": self.content_hash,
            "composes_into": list(self.composes_into),
            "evidence_policy": self.evidence_policy,
            "granted_tools": list(self.granted_tools),
            "token_budget": self.token_budget,
            "clamps": list(self.clamps),
            "output_contract": self.output_contract,
            "estimated_cost_gbp": self.estimated_cost_gbp,
        }


def validate_skill_source(
    source: str, *, settings: Settings, router: Router | None = None
) -> SkillPreview:
    """Parse one skill file and, if it is a custom section, compose what it would run as.

    Never raises for a bad file and never writes anything: this is the editor's own
    feedback loop, and an author mid-sentence produces invalid files constantly.
    """
    try:
        parsed = parse_skill_file(source)
    except SkillFileError as refused:
        return SkillPreview(
            valid=False,
            issues=[
                {"line": issue.line, "field": issue.field, "message": issue.message}
                for issue in refused.issues
            ],
        )

    # Methodology, preference and house-view skills compose into an existing agent's
    # prompt and carry no policy of their own — there is nothing to clamp and nothing to
    # estimate, and inventing a policy panel for them would imply otherwise. What the
    # editor can say instead is which roles will read the text (ADR 0108).
    preview = SkillPreview(
        valid=True,
        key=parsed.frontmatter.key,
        kind=parsed.frontmatter.kind.value,
        title=parsed.frontmatter.title,
        body=parsed.body,
        content_hash=parsed.content_hash,
        composes_into=list(roles_for(parsed.frontmatter.kind)),
    )
    if parsed.frontmatter.kind is not SkillKind.CUSTOM_SECTION:
        return preview

    composed = compose_for_version(_unsaved_version(parsed), settings=settings)
    model = router.resolve("custom_section").model if router is not None else "claude-sonnet-5"
    return SkillPreview(
        valid=True,
        key=preview.key,
        kind=preview.kind,
        title=preview.title,
        body=preview.body,
        content_hash=preview.content_hash,
        evidence_policy={
            "min_sources": composed.evidence.min_sources,
            "requires_primary": composed.evidence.requires_primary,
            "max_tier": composed.evidence.max_tier,
            "allow_forward_looking": composed.evidence.allow_forward_looking,
        },
        granted_tools=sorted(composed.allowed_tools),
        token_budget=composed.token_budget,
        clamps=[
            {
                "field": clamp.field,
                "requested": clamp.requested,
                "effective": clamp.effective,
                "reason": clamp.reason,
            }
            for clamp in composed.clamps
        ],
        output_contract=contract_schema(parsed.frontmatter.output or {}),
        estimated_cost_gbp=str(
            estimate_custom_section_cost(
                model=model,
                token_budget=composed.token_budget,
                usd_to_gbp=settings.usd_to_gbp,
            )
        ),
    )


def _unsaved_version(parsed: ParsedSkill) -> SkillVersion:
    """A version row that is never added to a session, for composing a preview against.

    Built from the parsed file with exactly the fields
    :func:`~aer.services.skills.save_skill` would store, so composing this and composing
    the row a save produces are the same operation on the same values. Transient by
    design — the preview writes nothing, and a row that reached the session would make
    "validate" a write endpoint.
    """
    evidence = parsed.frontmatter.evidence_policy
    return SkillVersion(
        version=parsed.frontmatter.version,
        title=parsed.frontmatter.title,
        scope=parsed.frontmatter.scope,
        position=parsed.frontmatter.position,
        required=parsed.frontmatter.required,
        applicability=parsed.frontmatter.applicability.model_dump(),
        min_sources=evidence.min_sources if evidence else None,
        requires_primary=evidence.requires_primary if evidence else None,
        max_tier=evidence.max_tier if evidence else None,
        allow_forward_looking=evidence.allow_forward_looking if evidence else None,
        output_contract=parsed.frontmatter.output,
        token_budget=parsed.frontmatter.token_budget,
        allowed_tools=list(parsed.frontmatter.allowed_tools),
        charts=list(parsed.frontmatter.charts),
        body=parsed.body,
        source=parsed.source,
        content_hash=parsed.content_hash,
    )


@dataclass(frozen=True, slots=True)
class ImportDiff:
    """What importing a file would change, and the hash confirming it requires."""

    key: str
    valid: bool
    issues: list[dict[str, Any]]

    # None where the key is new — an import that adds a skill rather than replacing one.
    current_version: int | None
    current_hash: str | None
    incoming_hash: str

    # Unified diff of the stored source against the incoming one, empty when identical.
    diff: list[str]
    payload_hash: str

    @property
    def is_new(self) -> bool:
        return self.current_version is None

    @property
    def is_identical(self) -> bool:
        return self.current_hash == self.incoming_hash

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "valid": self.valid,
            "issues": list(self.issues),
            "current_version": self.current_version,
            "current_hash": self.current_hash,
            "incoming_hash": self.incoming_hash,
            "is_new": self.is_new,
            "is_identical": self.is_identical,
            "diff": list(self.diff),
            "payload_hash": self.payload_hash,
        }


def import_payload_hash(*, key: str, incoming_hash: str, current_hash: str | None) -> str:
    """The hash an import confirmation carries.

    Over the key and **both** hashes, so a confirmation is of one specific replacement:
    this file, replacing that version. A hash over the incoming file alone would still
    confirm after somebody else saved a new version underneath — which is the state a
    confirmation exists to catch.
    """
    return sha256_hex(
        canonical_json({"key": key, "incoming": incoming_hash, "current": current_hash})
    )


async def import_diff(session: AsyncSession, *, source: str) -> ImportDiff:
    """What an import of this file would do, without doing any of it.

    A file that fails validation is reported invalid **with its issues and no diff**: a
    diff of something that cannot be saved would invite confirming it.
    """
    try:
        parsed = parse_skill_file(source)
    except SkillFileError as refused:
        issues = [
            {"line": issue.line, "field": issue.field, "message": issue.message}
            for issue in refused.issues
        ]
        return ImportDiff(
            key="",
            valid=False,
            issues=issues,
            current_version=None,
            current_hash=None,
            incoming_hash=sha256_hex(source.encode("utf-8")),
            diff=[],
            payload_hash="",
        )

    key = parsed.frontmatter.key
    latest = await current_version(session, key=key)
    stored = latest.source if latest is not None else ""

    diff = list(
        difflib.unified_diff(
            stored.splitlines(),
            parsed.source.splitlines(),
            fromfile=f"{key} v{latest.version}" if latest is not None else "(new skill)",
            tofile=f"{key} (incoming)",
            n=DIFF_CONTEXT_LINES,
            lineterm="",
        )
    )
    return ImportDiff(
        key=key,
        valid=True,
        issues=[],
        current_version=latest.version if latest is not None else None,
        current_hash=latest.content_hash if latest is not None else None,
        incoming_hash=parsed.content_hash,
        diff=diff,
        payload_hash=import_payload_hash(
            key=key,
            incoming_hash=parsed.content_hash,
            current_hash=latest.content_hash if latest is not None else None,
        ),
    )
