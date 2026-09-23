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
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Final

import structlog
from pydantic import SecretStr
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from aer.config import HouseStyle, ModelRoute, Settings
from aer.core.assumption_scales import PLAUSIBLE_RANGE, assumption_words
from aer.db.models import AuditEvent, SettingsOverride, User
from aer.errors import ValidationError

__all__ = [
    "OVERRIDABLE",
    "STANDING",
    "STANDING_NAMES",
    "StandingAssumption",
    "StandingName",
    "current_overrides",
    "effective_settings",
    "save_override",
    "save_standing_assumption",
    "secret_presence",
    "standing_assumptions",
    "standing_justification_key",
    "standing_value_key",
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
        key="price_move_threshold_pct",
        label="Price move worth telling you about (%)",
        help_text=(
            "For a watched listing that set no threshold of its own: the move over its "
            "window, up or down, past which the daily pass leaves a finding beside the "
            "thesis state. Too many dismissals mean this is wrong, not the market."
        ),
    ),
    Overridable(
        key="budget_warn_ratio",
        label="Warn at (fraction of budget)",
        help_text="Where the console starts warning, as a fraction between 0 and 1.",
    ),
    Overridable(
        key="house_style",
        label="House style",
        help_text=(
            "How the note presents itself: money scaling in prose, the date format, and "
            "the writing register. Presentation only — stored values are never restyled."
        ),
    ),
)

# -- Standing operator assumptions (ADR 0124) ----------------------------------------------


@dataclass(frozen=True, slots=True)
class StandingName:
    """A name that may have a standing value, and how the settings page asks for it."""

    name: str
    label: str
    help_text: str


STANDING: Final[tuple[StandingName, ...]] = (
    StandingName(
        name="equity_risk_premium",
        label="Equity risk premium",
        help_text=(
            "The premium over the risk-free rate the market demands for holding equities, "
            "as a decimal fraction — 5.5% is 0.055. A judgement no series publishes: say "
            "whose estimate it is and where it comes from."
        ),
    ),
)
"""Every name that may stand. Short on purpose: a judgement about the market, never a
company's own figure (the same number for every company is the wrong number for most of
them) and never one a source could supply (that would be the typed rate ADR 0082 calls an
attestation). A candidate joins by amending ADR 0124, not by adding a row here."""

STANDING_NAMES: Final[tuple[str, ...]] = tuple(item.name for item in STANDING)


def standing_value_key(name: str) -> str:
    """The settings key holding a standing value — also its `Settings` field."""
    return f"standing_{name}"


def standing_justification_key(name: str) -> str:
    """The settings key holding the justification beside it."""
    return f"standing_{name}_justification"


_STANDING_VALUE_KEYS: Final[frozenset[str]] = frozenset(
    standing_value_key(name) for name in STANDING_NAMES
)
_STANDING_JUSTIFICATION_KEYS: Final[frozenset[str]] = frozenset(
    standing_justification_key(name) for name in STANDING_NAMES
)

_KEYS: Final[frozenset[str]] = (
    frozenset(item.key for item in OVERRIDABLE)
    | _STANDING_VALUE_KEYS
    | _STANDING_JUSTIFICATION_KEYS
)


def _standing_name_of(key: str) -> str:
    return key.removeprefix("standing_").removesuffix("_justification")


def _standing_complaint(name: str, value: Decimal) -> str | None:
    """Why the value looks like a typing mistake, in the settings form's own words.

    The same band the assumptions service holds a proposal to at the gate, so a standing
    value cannot be one the gate would then refuse.
    """
    bounds = PLAUSIBLE_RANGE.get(name)
    if bounds is None:
        return None
    low, high = bounds
    if low <= value <= high:
        return None
    return (
        f"{value} is outside the plausible range for the {assumption_words(name) or name} "
        f"({low} to {high}). Rates and ratios are decimal fractions here: 5.5% is 0.055."
    )


@dataclass(frozen=True, slots=True)
class StandingAssumption:
    """A standing value as a run receives it: the number, the reason, who set it and when."""

    name: str
    value: Decimal
    justification: str
    # From the settings table where the value came from it; "the environment" and no date
    # for one that came from `.env`, which nobody recorded setting.
    set_by: str
    set_on: date | None

    @property
    def proposal_justification(self) -> str:
        """The row's justification: the operator's reason, then the value's provenance."""
        when = f" on {self.set_on.isoformat()}" if self.set_on is not None else ""
        return f"{self.justification} Standing value set in settings by {self.set_by}{when}."


async def save_standing_assumption(
    session: AsyncSession, *, name: str, value: str, justification: str, actor: User
) -> StandingAssumption | None:
    """Store a standing value with its justification, or clear both — never one alone.

    Both boxes empty clears the value, and the clearing is recorded. Returns what now
    stands, or ``None`` once cleared.

    Raises:
        ValidationError: If the name may not stand, the value does not parse or is outside
            the plausible band, or a value arrives with no justification. A premium set
            with no reason would be a number nobody explained (ADR 0124).
    """
    if name not in STANDING_NAMES:
        message = (
            f"{name!r} is not an assumption that may have a standing value. Only a judgement "
            f"about the market may: {', '.join(STANDING_NAMES)}."
        )
        raise ValidationError(message, context={"name": name})
    value_key = standing_value_key(name)
    why_key = standing_justification_key(name)
    words = assumption_words(name) or name

    if not value.strip() and not justification.strip():
        await session.execute(
            delete(SettingsOverride).where(SettingsOverride.key.in_([value_key, why_key]))
        )
        previous = await session.scalar(select(AuditEvent).order_by(AuditEvent.id.desc()).limit(1))
        session.add(
            AuditEvent.create_linked(
                actor=actor.email,
                event_type="settings.changed",
                payload={"key": value_key, "value": None},
                previous=previous,
            )
        )
        _log.info("configuration.standing_cleared", name=name, actor=actor.email)
        return None
    if not justification.strip():
        message = (
            f"Say why this is the {words} you are using. A standing value is proposed into "
            "every run with its justification, and a blank one would be a number nobody "
            "explained."
        )
        raise ValidationError(message, context={"name": name})
    if not value.strip():
        message = f"A justification with no {words} beside it stands for nothing; enter both."
        raise ValidationError(message, context={"name": name})

    stored = await save_override(session, key=value_key, raw=value, actor=actor)
    await save_override(session, key=why_key, raw=justification, actor=actor)
    return StandingAssumption(
        name=name,
        value=Decimal(str(stored)),
        justification=justification.strip(),
        set_by=actor.email,
        set_on=datetime.now(UTC).date(),
    )


async def standing_assumptions(
    session: AsyncSession, settings: Settings
) -> tuple[StandingAssumption, ...]:
    """The standing values in force for a run starting now, with who set each and when.

    Read from the effective settings, so a value from ``.env`` counts as much as one saved on
    the page; a value with no justification beside it does not count at all, because the
    gate would refuse to propose it. The provenance comes from the settings table where the
    value came from there.
    """
    effective = await effective_settings(session, settings)
    rows = {row.key: row for row in await session.scalars(select(SettingsOverride))}
    held: list[StandingAssumption] = []
    for name in STANDING_NAMES:
        value = getattr(effective, standing_value_key(name), None)
        justification = str(getattr(effective, standing_justification_key(name), "") or "")
        if value is None or not justification.strip():
            continue
        row = rows.get(standing_value_key(name))
        held.append(
            StandingAssumption(
                name=name,
                value=Decimal(str(value)),
                justification=justification.strip(),
                set_by=row.updated_by if row is not None else "the environment",
                set_on=row.updated_at.date() if row is not None else None,
            )
        )
    return tuple(held)


async def current_overrides(session: AsyncSession) -> dict[str, Any]:
    """Every override in force, by setting name."""
    rows = await session.scalars(select(SettingsOverride))
    return {row.key: row.value for row in rows if row.key in _KEYS}


async def effective_house_style(session: AsyncSession) -> HouseStyle:
    """The house style in force: the stored override when one validates, else defaults.

    A convenience for render surfaces that hold a session and no ``Settings`` object —
    the report preview, the run document. The same fall-back-and-warn posture as
    :func:`effective_settings`: a stored style that no longer validates formats nothing.
    """
    stored = (await current_overrides(session)).get("house_style")
    if stored is None:
        return HouseStyle()
    try:
        return HouseStyle.model_validate(stored)
    except PydanticValidationError:
        _log.warning("configuration.override_invalid", key="house_style")
        return HouseStyle()


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


# The overridable settings whose value is a JSON object rather than a scalar.
_OBJECT_KEYS: Final[frozenset[str]] = frozenset({"model_routes", "house_style"})

# A percentage's ceiling, for the one overridable that is one.
_WHOLE_PRICE: Final = Decimal(100)


def _parsed(key: str, raw: str) -> Any:
    if key not in _OBJECT_KEYS:
        return raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        message = f"The {key} value is not valid JSON: {exc}."
        raise ValidationError(message, context={"key": key}) from exc


def _coerce(key: str, raw: Any) -> Any:
    """A stored or submitted value as the type ``Settings`` declares for it."""
    if key == "model_routes":
        if not isinstance(raw, Mapping):
            message = "The routing table must be an object mapping each role to a model."
            raise ValidationError(message, context={"key": key})
        return {role: ModelRoute.model_validate(route) for role, route in raw.items()}
    if key == "house_style":
        if not isinstance(raw, Mapping):
            message = "The house style must be an object; every field it omits keeps its default."
            raise ValidationError(message, context={"key": key})
        try:
            return HouseStyle.model_validate(raw)
        except PydanticValidationError as exc:
            first = exc.errors()[0]
            field = ".".join(str(part) for part in first["loc"]) or "house_style"
            message = f"The house style is not valid: {field}: {first['msg']}."
            raise ValidationError(message, context={"key": key}) from exc
    if key == "budget_warn_ratio":
        ratio = float(raw)
        if not 0 < ratio <= 1:
            message = f"The warning ratio must be above 0 and at most 1; got {ratio}."
            raise ValidationError(message, context={"key": key})
        return ratio
    if key in _STANDING_VALUE_KEYS:
        return _standing_value(key, raw)
    if key in _STANDING_JUSTIFICATION_KEYS:
        return "" if raw is None else str(raw).strip()

    amount = Decimal(str(raw))
    if amount <= 0:
        message = f"{key} must be above zero; got {amount}."
        raise ValidationError(message, context={"key": key})
    if key == "price_move_threshold_pct" and amount > _WHOLE_PRICE:
        # `model_copy(update=)` in `effective_settings` validates nothing, so the field's own
        # ceiling is repeated here: a move past the whole of a price is a delisting, not a
        # threshold.
        message = f"The price move threshold is a percentage, at most 100; got {amount}."
        raise ValidationError(message, context={"key": key})
    return amount


def _standing_value(key: str, raw: Any) -> Decimal | None:
    """A standing assumption's value (ADR 0124): blank means unset.

    A value is held to the same plausible band the assumptions service applies at the gate,
    so nothing can stand that every run would then refuse.
    """
    text = "" if raw is None else str(raw).strip()
    if not text:
        return None
    name = _standing_name_of(key)
    try:
        value = Decimal(text)
    except InvalidOperation as exc:
        message = (
            f"The {assumption_words(name) or name} must be a decimal fraction — 5.5% is "
            f"0.055 — and {text!r} is not one."
        )
        raise ValidationError(message, context={"key": key}) from exc
    complaint = _standing_complaint(name, value)
    if complaint is not None:
        raise ValidationError(complaint, context={"key": key})
    return value


def _storable(value: Any) -> Any:
    """The JSON form of a coerced value, for the column and the audit payload."""
    if isinstance(value, HouseStyle):
        # mode="json" so the Decimal threshold stores as a string JSONB can hold exactly.
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return {role: route.model_dump() for role, route in value.items()}
    if isinstance(value, Decimal):
        return str(value)
    return value
