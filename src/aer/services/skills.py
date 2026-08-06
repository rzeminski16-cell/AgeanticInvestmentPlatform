"""Saving, versioning and switching skills — never executing them.

The write path is where the acceptance criterion lives: **a skill row cannot exist with
invalid frontmatter**, because :func:`save_skill` parses and validates before it
constructs anything, and a file the validator refused never reaches the session.

**Every save is a new version.** There is no update; there is version n+1. The version
number is allocated here, from what is stored — the author's own ``version`` field is
recorded inside the source but not trusted for ordering, because two edits both claiming
``version: 3`` should not be able to fight over history. Saving byte-identical content is
refused rather than recorded: a version that changes nothing is noise in the exact place
an audit needs signal.

**A key keeps its kind.** A ``methodology`` skill becoming a ``custom_section`` under the
same key would change what the key *means* everywhere it is pinned; that is a new skill
with a new key, not an edit.

Enabling is separate from saving, and everything starts disabled — authoring a skill and
turning it loose on runs are different decisions, the same split as proposing and
confirming an assumption.
"""

from __future__ import annotations

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.db.models import Skill, SkillVersion, User
from aer.errors import ValidationError
from aer.skills.frontmatter import ParsedSkill, parse_skill_file

__all__ = [
    "current_version",
    "list_skills",
    "save_skill",
    "set_enabled",
    "versions_of",
]

_log = structlog.get_logger("aer.services.skills")


async def save_skill(session: AsyncSession, *, source: str, actor: User) -> SkillVersion:
    """Validate a skill file and store it as the next version of its key.

    Raises:
        SkillFileError: If the frontmatter does not validate — with every issue and its
            line, and nothing written.
        ValidationError: If the content is byte-identical to the current version, or the
            key already exists with a different kind.
    """
    parsed = parse_skill_file(source)
    frontmatter = parsed.frontmatter

    skill = await session.scalar(select(Skill).where(Skill.key == frontmatter.key))
    if skill is None:
        skill = Skill(key=frontmatter.key, kind=frontmatter.kind.value, enabled=False)
        session.add(skill)
        await session.flush()
    elif skill.kind != frontmatter.kind.value:
        message = (
            f"The skill {frontmatter.key!r} is a {skill.kind}; this file declares "
            f"{frontmatter.kind.value}. A key keeps its kind — everywhere the key is "
            "pinned would silently change meaning. A different kind is a different "
            "skill with a different key."
        )
        raise ValidationError(
            message,
            context={
                "key": frontmatter.key,
                "kind": skill.kind,
                "declared": frontmatter.kind.value,
            },
        )

    latest = await current_version(session, key=frontmatter.key)
    if latest is not None and latest.content_hash == parsed.content_hash:
        message = (
            f"This is byte-for-byte the current version of {frontmatter.key!r} "
            f"(v{latest.version}). Saving it again would record an edit that changed "
            "nothing."
        )
        raise ValidationError(
            message, context={"key": frontmatter.key, "content_hash": parsed.content_hash}
        )

    row = _version_row(
        parsed, skill=skill, version=(latest.version if latest else 0) + 1, actor=actor
    )
    session.add(row)
    await session.flush()

    _log.info(
        "skill.saved",
        key=frontmatter.key,
        kind=frontmatter.kind.value,
        version=row.version,
        content_hash=parsed.content_hash,
        saved_by=actor.email,
    )
    return row


def _version_row(parsed: ParsedSkill, *, skill: Skill, version: int, actor: User) -> SkillVersion:
    frontmatter = parsed.frontmatter
    evidence = frontmatter.evidence_policy
    return SkillVersion(
        skill_id=skill.id,
        version=version,
        title=frontmatter.title,
        scope=frontmatter.scope,
        position=frontmatter.position,
        required=frontmatter.required,
        applicability=frontmatter.applicability.model_dump(),
        min_sources=evidence.min_sources if evidence else None,
        requires_primary=evidence.requires_primary if evidence else None,
        max_tier=evidence.max_tier if evidence else None,
        allow_forward_looking=evidence.allow_forward_looking if evidence else None,
        output_contract=frontmatter.output,
        token_budget=frontmatter.token_budget,
        allowed_tools=list(frontmatter.allowed_tools),
        charts=list(frontmatter.charts),
        body=parsed.body,
        source=parsed.source,
        content_hash=parsed.content_hash,
        created_by=actor.id,
    )


async def current_version(session: AsyncSession, *, key: str) -> SkillVersion | None:
    """The latest saved version of a skill, or None if the key is unknown."""
    found: SkillVersion | None = await session.scalar(
        select(SkillVersion)
        .join(Skill, Skill.id == SkillVersion.skill_id)
        .where(Skill.key == key)
        .order_by(SkillVersion.version.desc())
        .limit(1)
    )
    return found


async def versions_of(session: AsyncSession, *, key: str) -> list[SkillVersion]:
    """Every version of a skill, oldest first — the history the editor shows."""
    return list(
        await session.scalars(
            select(SkillVersion)
            .join(Skill, Skill.id == SkillVersion.skill_id)
            .where(Skill.key == key)
            .order_by(SkillVersion.version)
        )
    )


async def list_skills(session: AsyncSession) -> list[Skill]:
    return list(await session.scalars(select(Skill).order_by(Skill.key)))


async def set_enabled(session: AsyncSession, *, key: str, enabled: bool, actor: User) -> Skill:
    """Turn a skill on or off for future plans. Pinned runs are unaffected either way.

    Raises:
        ValidationError: If the key is unknown. Enabling a skill that was never saved is
            a typo, not a request.
    """
    skill = await session.scalar(select(Skill).where(Skill.key == key))
    if skill is None:
        message = f"No skill is named {key!r}."
        raise ValidationError(message, context={"key": key})

    skill.enabled = enabled
    await session.flush()
    _log.info("skill.enabled" if enabled else "skill.disabled", key=key, by=actor.email)
    return skill
