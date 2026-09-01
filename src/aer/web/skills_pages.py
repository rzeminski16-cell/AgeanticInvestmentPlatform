"""The authoring surface: library, editor, import and dry-run, server-rendered.

Task 43. The pages an operator writes a skill in. Every one of them posts a plain form
and every one of them calls the same services the JSON API calls — the editor is a second
*interface*, never a second implementation, and the rule a form enforces is the rule the
composer enforces because it is literally the same function.

**The composed-policy preview is the point of the editor.** A skill file's evidence policy
is a *request*: the additive-only composer takes the stricter of it and the platform floor,
intersects the tools with the role allowlist, and clamps the budget. An author shown only
what they typed would believe the request. So the editor validates on save-preview and
renders the effective policy beside every clamp, with the reason — the same receipts §2.4
shows at gate 2 when a run executes under one.

**No JavaScript.** Validation is a POST that re-renders the editor with the issues beside
the lines. A page whose errors need a script is a page that silently accepts anything when
the script does not load, and this one commissions spending.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Request
from sqlalchemy import select
from starlette.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from starlette.status import HTTP_303_SEE_OTHER, HTTP_403_FORBIDDEN, HTTP_404_NOT_FOUND

from aer.api.deps import (
    CurrentUser,
    DbSession,
    ProviderDep,
    RouterDep,
    SettingsDep,
    StoreDep,
)
from aer.core.enums import JobStatus
from aer.db.models import Job, ResearchRequest, Skill
from aer.errors import AerError
from aer.services import skills as skill_service
from aer.services.mandate import mandate_of
from aer.services.skill_authoring import import_diff, validate_skill_source
from aer.services.skill_dry_run import DRY_RUN_WORKFLOW, dry_run_skill
from aer.skills.frontmatter import SkillFileError
from aer.skills.library import starter_library
from aer.web.csrf import CSRF_FIELD_NAME, csrf_is_valid, new_csrf_token, set_csrf_cookie
from aer.web.templating import render

__all__ = ["router"]

router = APIRouter(include_in_schema=False)

# The file an empty editor starts from. A working example rather than a blank box: the
# frontmatter is the half people get wrong, and a valid skeleton teaches its shape faster
# than the schema documentation does.
STARTER_SOURCE = """\
---
aer_skill: 1
key: my_section
kind: custom_section
title: "My Section"
version: 1
scope: global
required: false
evidence_policy:
  min_sources: 2
  requires_primary: true
  max_tier: 4
output:
  summary: string
token_budget: 8000
allowed_tools: [search_facts, search_sources]
---

## What I want from this section

Describe what you want analysed, and what you do not want. This half is prose: the model
reads it. Everything above the fence is structured, and the platform acts on it.
"""


@router.get("/skills", response_class=HTMLResponse, summary="The skills library")
async def skills_library(
    request: Request,
    session: DbSession,
    settings: SettingsDep,
    user: CurrentUser,  # noqa: ARG001 -- the auth dependency, needed whether or not read
) -> Response:
    """Every saved skill, its current version, and whether runs will pick it up."""
    rows = await skill_service.list_skills(session)
    listed = []
    for skill in rows:
        latest = await skill_service.current_version(session, key=skill.key)
        listed.append(
            {
                "key": skill.key,
                "kind": skill.kind,
                "enabled": skill.enabled,
                "title": latest.title if latest else "",
                "version": latest.version if latest else None,
                "content_hash": latest.content_hash if latest else "",
            }
        )

    token = new_csrf_token(settings)
    response: Response = render(
        request,
        "skills/list.html",
        {"skills": listed, "csrf_field": CSRF_FIELD_NAME, "csrf_token": token},
    )
    set_csrf_cookie(response, token)
    return response


@router.get("/skills/new", response_class=HTMLResponse, summary="Write a new skill")
async def new_skill(
    request: Request,
    settings: SettingsDep,
    user: CurrentUser,  # noqa: ARG001 -- the auth dependency
) -> Response:
    return _editor(request, settings, source=STARTER_SOURCE, key=None, preview=None)


@router.get("/skills/examples", response_class=HTMLResponse, summary="The starter library")
async def examples_page(request: Request, settings: SettingsDep, user: CurrentUser) -> Response:
    """Worked examples to read, import and edit into something of your own.

    Listed rather than installed. An example reaches the platform through the ordinary
    import path, diff and confirmation included — pre-installing them would make that step
    look optional, which is the habit the diff exists to prevent (threat T20).
    """
    del user
    token = new_csrf_token(settings)
    page: Response = render(
        request,
        "skills/examples.html",
        {"examples": starter_library(), "csrf_field": CSRF_FIELD_NAME, "csrf_token": token},
    )
    set_csrf_cookie(page, token)
    return page


@router.get("/skills/{key}/export", summary="Download a skill file")
async def export_skill(key: str, session: DbSession, user: CurrentUser) -> Response:
    """The stored source, byte for byte, as a file.

    The *source* rather than a re-serialisation of the parsed frontmatter: what comes back
    must be what would go in, or a round trip through export and import would rewrite an
    operator's file — reordering keys, dropping comments — and the import diff would show
    changes nobody made.
    """
    del user
    version = await skill_service.current_version(session, key=key)
    if version is None:
        return PlainTextResponse(f"No skill {key}.", status_code=HTTP_404_NOT_FOUND)

    return PlainTextResponse(
        version.source,
        media_type="text/markdown; charset=utf-8",
        headers={"content-disposition": f'attachment; filename="{key}.md"'},
    )


@router.get("/skills/import", response_class=HTMLResponse, summary="Import a skill file")
async def import_page(
    request: Request,
    settings: SettingsDep,
    user: CurrentUser,  # noqa: ARG001 -- the auth dependency
) -> Response:
    token = new_csrf_token(settings)
    response: Response = render(
        request,
        "skills/import.html",
        {
            "source": "",
            "diff": None,
            "problem": None,
            "csrf_field": CSRF_FIELD_NAME,
            "csrf_token": token,
        },
    )
    set_csrf_cookie(response, token)
    return response


@router.get("/skills/{key}", response_class=HTMLResponse, summary="Edit a skill")
async def edit_skill(
    request: Request, key: str, session: DbSession, settings: SettingsDep, user: CurrentUser
) -> Response:
    """The editor, open on the current version's exact source bytes.

    Round-tripping the stored ``source`` rather than re-serialising the frontmatter is
    what makes an edit an edit: the content hash is over the file as written, so a
    reformatted version would be a different skill by the platform's own reckoning.
    """
    version = await skill_service.current_version(session, key=key)
    if version is None:
        return _missing(request, key)

    preview = validate_skill_source(version.source, settings=settings)
    return _editor(
        request,
        settings,
        source=version.source,
        key=key,
        preview=preview.as_dict(),
        version=version.version,
        runs=await _dry_run_targets(session, user=user),
    )


@router.post("/skills/validate", summary="Validate without saving")
async def validate_page(
    request: Request, session: DbSession, settings: SettingsDep, user: CurrentUser
) -> Response:
    """Re-render the editor with the issues, or with the composed policy. Writes nothing."""
    form = await request.form()
    if not csrf_is_valid(request, str(form.get(CSRF_FIELD_NAME, "")), settings):
        return _forbidden(request)

    source = str(form.get("source", ""))
    key = str(form.get("key", "")) or None
    preview = validate_skill_source(source, settings=settings)
    return _editor(
        request,
        settings,
        source=source,
        key=key,
        preview=preview.as_dict(),
        runs=await _dry_run_targets(session, user=user),
    )


@router.post("/skills/save", summary="Save a skill")
async def save_page(
    request: Request, session: DbSession, settings: SettingsDep, user: CurrentUser
) -> Response:
    """Save, or re-render the editor with the reason it could not be saved."""
    form = await request.form()
    if not csrf_is_valid(request, str(form.get(CSRF_FIELD_NAME, "")), settings):
        return _forbidden(request)

    source = str(form.get("source", ""))
    key = str(form.get("key", "")) or None
    try:
        version = await skill_service.save_skill(session, source=source, actor=user)
    except SkillFileError:
        # The issues are in the preview, beside the lines they belong to — which is where
        # an author fixes them, so the editor re-renders rather than reporting an error.
        preview = validate_skill_source(source, settings=settings)
        return _editor(request, settings, source=source, key=key, preview=preview.as_dict())
    except AerError as refused:
        preview = validate_skill_source(source, settings=settings)
        return _editor(
            request,
            settings,
            source=source,
            key=key,
            preview=preview.as_dict(),
            problem=str(refused),
        )

    saved = await session.get(Skill, version.skill_id)
    await session.commit()
    return RedirectResponse(f"/skills/{saved.key if saved else ''}", status_code=HTTP_303_SEE_OTHER)


@router.post("/skills/{key}/enable", summary="Enable or disable a skill")
async def enable_page(
    request: Request, key: str, session: DbSession, settings: SettingsDep, user: CurrentUser
) -> Response:
    form = await request.form()
    if not csrf_is_valid(request, str(form.get(CSRF_FIELD_NAME, "")), settings):
        return _forbidden(request)

    enabled = str(form.get("enabled", "")).lower() == "true"
    await skill_service.set_enabled(session, key=key, enabled=enabled, actor=user)
    await session.commit()
    return RedirectResponse("/skills", status_code=HTTP_303_SEE_OTHER)


@router.post("/skills/import", summary="Show or confirm an import diff")
async def import_submit(
    request: Request, session: DbSession, settings: SettingsDep, user: CurrentUser
) -> Response:
    """Two steps in one route, and the difference is the confirmation.

    Without one, this shows the diff and nothing is written. With one, the hash is
    recomputed from what is stored now — an import confirmed against a version that has
    since moved is not a confirmation of this one (threat T20).
    """
    form = await request.form()
    if not csrf_is_valid(request, str(form.get(CSRF_FIELD_NAME, "")), settings):
        return _forbidden(request)

    source = str(form.get("source", ""))
    confirmed = str(form.get("payload_hash", ""))
    diff = await import_diff(session, source=source)

    if confirmed and diff.valid and confirmed == diff.payload_hash:
        version = await skill_service.save_skill(session, source=source, actor=user)
        saved = await session.get(Skill, version.skill_id)
        await session.commit()
        return RedirectResponse(
            f"/skills/{saved.key if saved else ''}", status_code=HTTP_303_SEE_OTHER
        )

    problem = None
    if confirmed and confirmed != diff.payload_hash:
        problem = (
            "The stored version changed since this diff was shown, so the confirmation "
            "no longer describes the import it would make. Review the diff below."
        )

    token = new_csrf_token(settings)
    response: Response = render(
        request,
        "skills/import.html",
        {
            "source": source,
            "diff": diff.as_dict(),
            "problem": problem,
            "csrf_field": CSRF_FIELD_NAME,
            "csrf_token": token,
        },
    )
    set_csrf_cookie(response, token)
    return response


@router.post("/skills/{key}/dry-run", response_class=HTMLResponse, summary="Try a skill")
async def dry_run_page(
    request: Request,
    key: str,
    *,
    session: DbSession,
    settings: SettingsDep,
    provider: ProviderDep,
    model_router: RouterDep,
    store: StoreDep,
    user: CurrentUser,
) -> Response:
    """Execute the skill against a chosen finished run and render what it produced."""
    form = await request.form()
    if not csrf_is_valid(request, str(form.get(CSRF_FIELD_NAME, "")), settings):
        return _forbidden(request)

    version = await skill_service.current_version(session, key=key)
    if version is None:
        return _missing(request, key)

    job = await _owned_job(session, job_id=str(form.get("job_id", "")), user=user)
    if job is None:
        return _editor(
            request,
            settings,
            source=version.source,
            key=key,
            preview=validate_skill_source(version.source, settings=settings).as_dict(),
            version=version.version,
            runs=await _dry_run_targets(session, user=user),
            problem="Choose one of your own finished runs to try this skill against.",
        )

    try:
        outcome = await dry_run_skill(
            session,
            key=key,
            source_job=job,
            settings=settings,
            provider=provider,
            router=model_router,
            store=store,
        )
    except AerError as refused:
        return _editor(
            request,
            settings,
            source=version.source,
            key=key,
            preview=validate_skill_source(version.source, settings=settings).as_dict(),
            version=version.version,
            runs=await _dry_run_targets(session, user=user),
            problem=str(refused),
        )

    await session.commit()
    return _editor(
        request,
        settings,
        source=version.source,
        key=key,
        preview=validate_skill_source(version.source, settings=settings).as_dict(),
        version=version.version,
        runs=await _dry_run_targets(session, user=user),
        dry_run=outcome.as_dict(),
    )


# ==========================================================================================
# Helpers
# ==========================================================================================


def _editor(
    request: Request,
    settings: Any,
    *,
    source: str,
    key: str | None,
    preview: dict[str, Any] | None,
    version: int | None = None,
    runs: list[dict[str, Any]] | None = None,
    problem: str | None = None,
    dry_run: dict[str, Any] | None = None,
) -> Response:
    token = new_csrf_token(settings)
    response: Response = render(
        request,
        "skills/edit.html",
        {
            "source": source,
            "key": key,
            "version": version,
            "preview": preview,
            "runs": runs or [],
            "problem": problem,
            "dry_run": dry_run,
            "csrf_field": CSRF_FIELD_NAME,
            "csrf_token": token,
        },
    )
    set_csrf_cookie(response, token)
    return response


async def _dry_run_targets(session: DbSession, *, user: CurrentUser) -> list[dict[str, Any]]:
    """The caller's finished runs, newest first — what a dry run may borrow evidence from.

    Dry runs of their own are excluded: a rehearsal against a rehearsal's evidence would
    read a job that acquired nothing, and offering it would be offering a guaranteed
    empty section.
    """
    rows = await session.scalars(
        select(Job)
        .join(ResearchRequest, ResearchRequest.id == Job.work_order_id)
        .where(
            ResearchRequest.user_id == user.id,
            Job.workflow_version != DRY_RUN_WORKFLOW,
            Job.status.in_([JobStatus.SUCCEEDED, JobStatus.AWAITING_APPROVAL]),
        )
        .order_by(Job.started_at.desc(), Job.id)
        .limit(20)
    )
    targets: list[dict[str, Any]] = []
    for job in rows:
        research_request = await mandate_of(session, job)
        if research_request is None:  # pragma: no cover -- a job cannot exist without one
            continue
        targets.append(
            {
                "job_id": str(job.id),
                "label": (
                    f"{research_request.company_name} ({research_request.ticker}) "
                    f"as at {research_request.as_of_date.isoformat()}"
                ),
                "status": job.status.value,
            }
        )
    return targets


async def _owned_job(session: DbSession, *, job_id: str, user: CurrentUser) -> Job | None:
    try:
        identifier = uuid.UUID(job_id)
    except ValueError:
        return None
    job = await session.get(Job, identifier)
    if job is None:
        return None
    research_request = await mandate_of(session, job)
    if research_request is None or research_request.user_id != user.id:
        return None
    return job


def _missing(request: Request, key: str) -> Response:
    response: Response = render(
        request,
        "runs/problem.html",
        {"message": f"No skill is named {key!r}."},
        status_code=HTTP_404_NOT_FOUND,
    )
    return response


def _forbidden(request: Request) -> Response:
    response: Response = render(
        request,
        "runs/problem.html",
        {
            "message": (
                "The anti-forgery token was missing or stale. Reload the page and try "
                "again — nothing was saved."
            )
        },
        status_code=HTTP_403_FORBIDDEN,
    )
    return response
