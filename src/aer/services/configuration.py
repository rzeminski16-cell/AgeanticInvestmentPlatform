"""Settings an operator may change from the interface, and the ones they may not.

Gap B6/B11. Rebalancing cost against quality — routing `analysis` to Sonnet instead of
Opus, lowering the monthly cap — was an edit to `.env` and a restart. That is a poor
interface for the decision this platform asks its operator to make most often.

**Credentials are not on this list, and that is a decision rather than an omission.** The
plan asks for "provider-key management in settings", and putting a key in the database
would deliver it. It would also put every live credential into `pg_dump` output, which is
to say into every backup `aer backup` writes and into whatever directory those get copied
to. `.env` is one file, git-ignored, with one copy. See ``docs/adr/0050``.

**The allowlist is a closed vocabulary, checked in code.** A key that is not in
:data:`OVERRIDABLE` cannot be written, whatever a form posts — so a new secret field added
to `Settings` later is not silently editable, and a crafted request cannot reach one.

**An override applies to runs that start after it.** Configuration is read when a run
begins, not per step: a run whose model changed halfway through would have a provenance
record that describes two different platforms.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Final

import structlog
from pydantic import SecretStr
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from aer.config import ModelRoute, Settings
from aer.db.models import AuditEvent, SettingsOverride, User
from aer.errors import ValidationError

__all__ = [
    "OVERRIDABLE",
    "current_overrides",
    "effective_settings",
    "save_override",
    "secret_presence",
]

_log = structlog.get_logger("aer.services.configuration")


@dataclass(frozen=True, slots=True)
class Overridable:
    """One setting an operator may change, and how to read what they typed."""

    key: str
    label: str
    help_text: str


# Deliberately short. Every entry is a decision an operator makes about cost or method;
# nothing here is a credential, a path, or a switch that changes what the platform is
# allowed to do. Widening this needs a reason, not a form field.
OVERRIDABLE: Final[tuple[Overridable, ...]] = (
    Overridable(
        key="model_routes",
        label="Model routing",
        help_text=(
            "Which model answers for each agent role, and at what effort. The single "
            "largest lever on what a run costs."
        ),
    ),
    Overridable(
        key="per_run_budget_gbp",
        label="Budget per run (£)",
        help_text="A run stops when it reaches this, at its next step boundary.",
    ),
    Overridable(
        key="monthly_budget_gbp",
        label="Budget per month (£)",
        help_text="Spend across all runs in the calendar month.",
    ),
    Overridable(
        key="budget_warn_ratio",
        label="Warn at (fraction of budget)",
        help_text="Where the console starts warning, as a fraction between 0 and 1.",
    ),
)

_KEYS: Final[frozenset[str]] = frozenset(item.key for item in OVERRIDABLE)


async def current_overrides(session: AsyncSession) -> dict[str, Any]:
    """Every override in force, by setting name."""
    rows = await session.scalars(select(SettingsOverride))
    return {row.key: row.value for row in rows if row.key in _KEYS}


async def effective_settings(session: AsyncSession, base: Settings) -> Settings:
    """``base`` with the stored overrides applied.

    Returns the original object when nothing is overridden, so the ordinary path allocates
    nothing and a platform with an empty table behaves exactly as it did before this module
    existed.

    A stored value that no longer validates is **ignored with a warning**, not raised. The
    alternative is a platform that will not start because of a row in a settings table,
    which is a worse failure than running on the defaults and saying so.
    """
    stored = await current_overrides(session)
    if not stored:
        return base

    applied: dict[str, Any] = {}
    for key, raw in stored.items():
        try:
            applied[key] = _coerce(key, raw)
        except (
            ValidationError,
            PydanticValidationError,
            InvalidOperation,
            TypeError,
            ValueError,
        ) as exc:
            _log.warning("configuration.override_ignored", key=key, error=str(exc))

    if not applied:
        return base
    return base.model_copy(update=applied)


async def save_override(session: AsyncSession, *, key: str, raw: str, actor: User) -> Any:
    """Validate and store one override, recording who changed it.

    Raises:
        ValidationError: If the key is not overridable, or the value does not parse. Both
            are refusals rather than corrections: a budget silently clamped to something
            the operator did not type is a budget they do not know they have.
    """
    if key not in _KEYS:
        message = (
            f"{key!r} is not a setting that may be changed here. Credentials and paths live "
            "in .env by design — see docs/adr/0050 — and everything else is code."
        )
        raise ValidationError(message, context={"key": key})

    value = _coerce(key, _parsed(key, raw))

    await session.execute(
        insert(SettingsOverride)
        .values(
            key=key,
            value=_storable(value),
            updated_by=actor.email,
            updated_at=datetime.now(UTC),
        )
        .on_conflict_do_update(
            index_elements=[SettingsOverride.key],
            set_={
                "value": _storable(value),
                "updated_by": actor.email,
                "updated_at": datetime.now(UTC),
            },
        )
    )

    previous = await session.scalar(select(AuditEvent).order_by(AuditEvent.id.desc()).limit(1))
    session.add(
        AuditEvent.create_linked(
            actor=actor.email,
            event_type="settings.changed",
            # The value is recorded: a routing change is exactly the kind of thing somebody
            # wants to correlate against a month's spend afterwards.
            payload={"key": key, "value": _storable(value)},
            previous=previous,
        )
    )
    _log.info("configuration.override_saved", key=key, actor=actor.email)
    return value


def secret_presence(settings: Settings) -> dict[str, bool]:
    """Which credentials are configured. **Presence only, never the value.**

    The settings page shows this so an operator can see at a glance why a provider is
    unavailable, without the page ever holding a key it could leak into a screenshot, a
    browser cache or a bug report.
    """
    return {
        name: isinstance(getattr(settings, name, None), SecretStr)
        for name, field in Settings.model_fields.items()
        if _is_secret(field.annotation)
    }


def _is_secret(annotation: Any) -> bool:
    return annotation is SecretStr or (
        hasattr(annotation, "__args__") and SecretStr in getattr(annotation, "__args__", ())
    )


def _parsed(key: str, raw: str) -> Any:
    if key != "model_routes":
        return raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        message = f"The routing table is not valid JSON: {exc}."
        raise ValidationError(message, context={"key": key}) from exc


def _coerce(key: str, raw: Any) -> Any:
    """A stored or submitted value as the type ``Settings`` declares for it."""
    if key == "model_routes":
        if not isinstance(raw, Mapping):
            message = "The routing table must be an object mapping each role to a model."
            raise ValidationError(message, context={"key": key})
        return {role: ModelRoute.model_validate(route) for role, route in raw.items()}
    if key == "budget_warn_ratio":
        ratio = float(raw)
        if not 0 < ratio <= 1:
            message = f"The warning ratio must be above 0 and at most 1; got {ratio}."
            raise ValidationError(message, context={"key": key})
        return ratio

    amount = Decimal(str(raw))
    if amount <= 0:
        message = f"{key} must be above zero; got {amount}."
        raise ValidationError(message, context={"key": key})
    return amount


def _storable(value: Any) -> Any:
    """The JSON form of a coerced value, for the column and the audit payload."""
    if isinstance(value, Mapping):
        return {role: route.model_dump() for role, route in value.items()}
    if isinstance(value, Decimal):
        return str(value)
    return value
