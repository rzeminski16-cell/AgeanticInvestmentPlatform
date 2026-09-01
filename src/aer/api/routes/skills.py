"""The skills API: list, save, validate, import with a diff, and dry-run.

Task 43. Every write here goes through :mod:`aer.services.skills`, which validates before
it constructs — so a skill row cannot exist with invalid frontmatter no matter which
surface asked for it.

**Validate writes nothing and costs nothing.** It is the editor's own feedback loop, so it
returns issues with their lines rather than raising, and it returns the composed policy
beside them: the additive-only composer clamps what a file asked for, and an author who is
not shown the clamps will believe the request.

**Import requires confirming a diff** (threat T20). A file somebody else wrote is shown as
a diff against the stored version and refuses to save without a confirmation carrying the
hash of exactly that comparison — the same shape as a gate approval, because "I agree to
this replacement" and "I clicked import at some point" are different statements.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Body
from pydantic import BaseModel, ConfigDict, Field
from starlette.status import HTTP_201_CREATED, HTTP_404_NOT_FOUND

from aer.api.deps import CurrentUser, DbSession, ProviderDep, RouterDep, SettingsDep, StoreDep
from aer.db.models import Job, Skill
from aer.errors import AerError, ValidationError
from aer.services import skills as skill_service
from aer.services.mandate import mandate_of
from aer.services.skill_authoring import import_diff, import_payload_hash, validate_skill_source
from aer.services.skill_dry_run import dry_run_skill
from aer.skills.frontmatter import SkillFileError, parse_skill_file

__all__ = ["router"]

router = APIRouter(prefix="/api/skills", tags=["skills"])

# How much skill source one request may carry. Generous for a document a person typed,
# small enough that the endpoint is not a file upload in disguise.
MAX_SOURCE_CHARS = 100_000


class SkillNotFoundError(AerError):
    """No skill by that key."""

    code = "skill_not_found"
    http_status = HTTP_404_NOT_FOUND


class SaveRequest(BaseModel):
    """One skill file, as typed."""

    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=1, max_length=MAX_SOURCE_CHARS)


class ImportRequest(BaseModel):
    """An imported file, with the confirmation of the diff that was displayed.

    ``payload_hash`` absent means "show me the diff"; present means "I have read it".
    """

    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=1, max_length=MAX_SOURCE_CHARS)
    payload_hash: str | None = Field(default=None, min_length=64, max_length=64)


class EnableRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


class DryRunRequest(BaseModel):
    """Which finished run's evidence to try the skill against."""

    model_config = ConfigDict(extra="forbid")

    job_id: uuid.UUID


class SkillRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    kind: str
    enabled: bool
    title: str | None = None
    version: int | None = None
    content_hash: str | None = None


class SkillListRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skills: list[SkillRead]


@router.get("", response_model=SkillListRead, summary="Every saved skill")
async def list_skills(session: DbSession, user: CurrentUser) -> SkillListRead:  # noqa: ARG001
    rows = await skill_service.list_skills(session)
    return SkillListRead(skills=[await _read(session, skill) for skill in rows])


@router.post("", response_model=SkillRead, status_code=HTTP_201_CREATED, summary="Save a skill")
async def save_skill(
    body: Annotated[SaveRequest, Body()], session: DbSession, user: CurrentUser
) -> SkillRead:
    """Save a skill file as the next version of its key.

    ``POST`` rather than ``PUT`` for both the first save and every edit, because there is
    no update: there is version n+1. The key inside the file decides which skill it
    belongs to, so the caller does not name one twice.
    """
    version = await skill_service.save_skill(session, source=body.source, actor=user)
    skill = await session.get(Skill, version.skill_id)
    assert skill is not None
    await session.commit()
    return SkillRead(
        key=skill.key,
        kind=skill.kind,
        enabled=skill.enabled,
        title=version.title,
        version=version.version,
        content_hash=version.content_hash,
    )


@router.put("/{key}", response_model=SkillRead, summary="Save a new version of a skill")
async def update_skill(
    key: str, body: Annotated[SaveRequest, Body()], session: DbSession, user: CurrentUser
) -> SkillRead:
    """Save an edit, refusing a file whose key is not the one being edited.

    The check is the whole reason this route exists beside ``POST``: an editor open on one
    skill, saving a file whose frontmatter names another, would otherwise silently write a
    version of a skill nobody was looking at.
    """
    parsed_key = _key_of(body.source)
    if parsed_key is not None and parsed_key != key:
        message = (
            f"This editor is open on {key!r} but the file declares {parsed_key!r}. Saving "
            "would write a version of a skill you are not looking at; rename the key in "
            "the file, or create it as a new skill."
        )
        raise ValidationError(message, context={"key": key, "declared": parsed_key})
    return await save_skill(body, session, user)


@router.post("/validate", summary="Validate a skill file and preview what it composes to")
async def validate_skill(
    body: Annotated[SaveRequest, Body()],
    settings: SettingsDep,
    model_router: RouterDep,
    user: CurrentUser,  # noqa: ARG001
) -> dict[str, Any]:
    """Line-level errors, or the effective policy with every clamp. Writes nothing."""
    return validate_skill_source(body.source, settings=settings, router=model_router).as_dict()


@router.post("/import", summary="Import a skill file, confirming the diff")
async def import_skill(
    body: Annotated[ImportRequest, Body()], session: DbSession, user: CurrentUser
) -> dict[str, Any]:
    """Show what the import would change, or apply it against a confirmed diff.

    Without ``payload_hash`` this is a read: the diff, and the hash confirming it needs.
    With one, the hash is recomputed from what is stored *now* and must match — an import
    confirmed against a version that has since moved is not a confirmation of this one.
    """
    diff = await import_diff(session, source=body.source)
    if body.payload_hash is None:
        return {"applied": False, **diff.as_dict()}

    if not diff.valid:
        message = (
            "This file does not validate, so there is nothing to confirm. The issues are "
            "reported against the file's own lines."
        )
        raise ValidationError(message, context={"issues": diff.issues})

    expected = import_payload_hash(
        key=diff.key, incoming_hash=diff.incoming_hash, current_hash=diff.current_hash
    )
    if body.payload_hash != expected:
        message = (
            "The confirmation was recorded against a different comparison from the one "
            "this import would make — the stored version has changed since the diff was "
            "shown. Review the new diff and confirm that."
        )
        raise ValidationError(message, context={"confirmed": body.payload_hash, "actual": expected})

    version = await skill_service.save_skill(session, source=body.source, actor=user)
    applied = {"applied": True, **diff.as_dict(), "version": version.version}
    await session.commit()
    return applied


@router.post("/{key}/enable", response_model=SkillRead, summary="Enable or disable a skill")
async def set_enabled(
    key: str, body: Annotated[EnableRequest, Body()], session: DbSession, user: CurrentUser
) -> SkillRead:
    skill = await skill_service.set_enabled(session, key=key, enabled=body.enabled, actor=user)
    read = await _read(session, skill)
    await session.commit()
    return read


@router.post("/{key}/dry-run", summary="Try a skill against a finished run's evidence")
async def dry_run(
    key: str,
    body: Annotated[DryRunRequest, Body()],
    *,
    session: DbSession,
    settings: SettingsDep,
    provider: ProviderDep,
    model_router: RouterDep,
    store: StoreDep,
    user: CurrentUser,
) -> dict[str, Any]:
    """Execute one section against a previous run, and return it as it would have appeared.

    The chosen run must be the caller's own: a dry run reads a run's evidence and spends
    against its request's budget, and neither is somebody else's to lend.
    """
    job = await session.get(Job, body.job_id)
    if job is None or not await _owns(session, job=job, user=user):
        message = f"No run {body.job_id}."
        raise SkillNotFoundError(message, context={"job_id": str(body.job_id)})

    outcome = await dry_run_skill(
        session,
        key=key,
        source_job=job,
        settings=settings,
        provider=provider,
        router=model_router,
        store=store,
    )
    body_out = outcome.as_dict()
    await session.commit()
    return body_out


async def _owns(session: DbSession, *, job: Job, user: CurrentUser) -> bool:
    request = await mandate_of(session, job)
    return request is not None and request.user_id == user.id


async def _read(session: DbSession, skill: Skill) -> SkillRead:
    latest = await skill_service.current_version(session, key=skill.key)
    return SkillRead(
        key=skill.key,
        kind=skill.kind,
        enabled=skill.enabled,
        title=latest.title if latest else None,
        version=latest.version if latest else None,
        content_hash=latest.content_hash if latest else None,
    )


def _key_of(source: str) -> str | None:
    """The key a file declares, or ``None`` if it does not parse well enough to say."""
    try:
        return parse_skill_file(source).frontmatter.key
    except SkillFileError:
        return None
