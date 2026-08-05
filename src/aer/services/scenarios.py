"""Resolving a scenario against the base case, and recording a sensitivity grid.

**A scenario is a diff.** :func:`resolve` reads the confirmed assumptions and applies the
scenario's overrides on top. It does not read a stored copy of the base case, because there
is no stored copy — that is the whole design. Correcting the base case's tax rate improves
every scenario that did not explicitly disagree about the tax rate, and the ones that did
keep their own figure with their own justification.

The failure this avoids is quiet. A bear case built by copying the base case and editing two
rows looks identical to one built as a diff, until somebody fixes a mistake in the base case
six weeks later. From then on every comparison between the two is measuring the correction as
well as the scenario, and nothing in the output says so.

**An override still needs a confirmed assumption underneath it.** Overriding a name that no
confirmed assumption has is refused: a scenario that introduces a number the base case never
had is not a scenario, it is a second model nobody reviewed.

**Every sensitivity cell names its calculation.** :func:`record_sensitivity` will not write a
cell without one. See :mod:`aer.db.models.sensitivity` on why a grid is the easiest thing in
a valuation to fake.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.calc.units import Quantity, SourceRef, Unit
from aer.db.models import Scenario, ScenarioOverride, Sensitivity, SensitivityCell
from aer.errors import ValidationError
from aer.services.assumptions import confirmed_values

__all__ = [
    "CellInput",
    "ResolvedScenario",
    "create_scenario",
    "record_sensitivity",
    "resolve",
    "scenarios_for_request",
    "sensitivities_for_request",
    "set_override",
]

_log = structlog.get_logger("aer.services.scenarios")


@dataclass(frozen=True, slots=True)
class ResolvedScenario:
    """The assumptions a scenario actually runs on, and which of them it changed."""

    key: str
    label: str
    values: Mapping[str, Quantity]

    # The names this scenario overrode. Separate from `values` because a report has to be
    # able to say "the bear case differs from the base case in these three assumptions and
    # no others", and diffing two dictionaries to find that out invites getting it wrong.
    overridden: tuple[str, ...]

    def differs_from_base_in(self) -> tuple[str, ...]:
        return self.overridden


async def create_scenario(
    session: AsyncSession,
    *,
    request_id: uuid.UUID,
    key: str,
    label: str,
    description: str,
    job_id: uuid.UUID | None = None,
) -> Scenario:
    """A named case. Its overrides are added separately, and it may have none.

    A scenario with no overrides is the base case under another name, which is a legitimate
    thing to want: it gives the base case a description and somewhere for a sensitivity grid
    to hang off.

    Raises:
        ValidationError: If the description is blank. A scenario is an argument, and one
            with no stated premise is a column of numbers.
    """
    if not description.strip():
        message = (
            f"The scenario {key!r} was created with no description. A scenario is an "
            "argument about what might happen; without its premise written down it is a "
            "column of numbers nobody can agree or disagree with."
        )
        raise ValidationError(message, context={"key": key})

    scenario = Scenario(
        request_id=request_id,
        job_id=job_id,
        key=key,
        label=label,
        description=description,
    )
    session.add(scenario)
    await session.flush()

    _log.info("scenario.created", scenario_id=str(scenario.id), key=key)
    return scenario


async def set_override(
    session: AsyncSession,
    *,
    scenario: Scenario,
    assumption_name: str,
    value: Decimal,
    unit: str,
    justification: str,
) -> ScenarioOverride:
    """State that this case disagrees with the base about one assumption.

    Setting the same name twice replaces the previous override rather than adding a second:
    a scenario that disagreed with itself would resolve to whichever row was read last.

    Raises:
        ValidationError: If the justification is blank, if the unit cannot be parsed, or if
            no confirmed assumption of that name exists. The third is the important one — a
            scenario may argue about a number the base case has, not introduce one it does
            not.
    """
    if not justification.strip():
        message = (
            f"The {scenario.key!r} override of {assumption_name!r} has no justification. "
            '"Bear case: growth 2%" is not an argument; the reason it is 2% is.'
        )
        raise ValidationError(
            message, context={"scenario": scenario.key, "assumption": assumption_name}
        )

    try:
        Unit.parse(unit)
    except Exception as exc:
        message = (
            f"The {scenario.key!r} override of {assumption_name!r} is stated in {unit!r}, "
            "which is not a unit this platform understands."
        )
        raise ValidationError(message, context={"scenario": scenario.key, "unit": unit}) from exc

    base = await confirmed_values(session, scenario.request_id)
    if assumption_name not in base:
        message = (
            f"The {scenario.key!r} case overrides {assumption_name!r}, which is not a "
            "confirmed assumption of this request. A scenario may argue about a number the "
            "base case has; introducing one it does not have is a second model nobody "
            "reviewed."
        )
        raise ValidationError(
            message,
            context={
                "scenario": scenario.key,
                "assumption": assumption_name,
                "confirmed": sorted(base),
            },
        )

    existing = await session.scalar(
        select(ScenarioOverride).where(
            ScenarioOverride.scenario_id == scenario.id,
            ScenarioOverride.assumption_name == assumption_name,
        )
    )
    if existing is not None:
        existing.value = value
        existing.unit = unit
        existing.justification = justification
        await session.flush()
        return existing

    override = ScenarioOverride(
        scenario_id=scenario.id,
        assumption_name=assumption_name,
        value=value,
        unit=unit,
        justification=justification,
    )
    session.add(override)
    await session.flush()

    _log.info(
        "scenario.override_set",
        scenario_id=str(scenario.id),
        key=scenario.key,
        assumption=assumption_name,
        value=str(value),
    )
    return override


async def resolve(session: AsyncSession, *, scenario: Scenario) -> ResolvedScenario:
    """The assumptions this case runs on: the confirmed base, with its overrides applied.

    **Read fresh, every time.** Nothing here is cached and nothing is stored: a base-case
    assumption amended and re-confirmed after this scenario was written propagates the next
    time the scenario is resolved, which is the property the whole design exists for.

    An overridden value's source is the *override* rather than the base assumption, so a
    figure computed in the bear case traces to the bear case's own reasoning rather than to
    the base case's.
    """
    values = dict(await confirmed_values(session, scenario.request_id))

    overrides = list(
        await session.scalars(
            select(ScenarioOverride)
            .where(ScenarioOverride.scenario_id == scenario.id)
            .order_by(ScenarioOverride.assumption_name)
        )
    )

    overridden: list[str] = []
    for override in overrides:
        # An override whose assumption has since been un-confirmed drops out with the base
        # case rather than resurrecting it. `set_override` refused this at write time; a
        # later amendment can still create it, and the scenario must not be the way an
        # unconfirmed number gets into a calculation.
        if override.assumption_name not in values:
            continue
        values[override.assumption_name] = Quantity.of(
            override.value,
            Unit.parse(override.unit),
            source=SourceRef.assumption(
                override.id, label=f"{scenario.key}:{override.assumption_name}"
            ),
        )
        overridden.append(override.assumption_name)

    return ResolvedScenario(
        key=scenario.key,
        label=scenario.label,
        values=values,
        overridden=tuple(overridden),
    )


async def scenarios_for_request(session: AsyncSession, request_id: uuid.UUID) -> list[Scenario]:
    rows = await session.scalars(
        select(Scenario).where(Scenario.request_id == request_id).order_by(Scenario.key)
    )
    return list(rows)


@dataclass(frozen=True, slots=True)
class CellInput:
    """One point of a grid: where it sits, what it produced, and what produced it."""

    x_value: Decimal
    y_value: Decimal
    output_value: Decimal

    # The `calculations` row. Mandatory in the type, not merely in the schema, so a caller
    # cannot construct a cell it has no calculation for and discover that at flush time.
    calculation_id: uuid.UUID


async def record_sensitivity(
    session: AsyncSession,
    *,
    request_id: uuid.UUID,
    label: str,
    x_assumption: str,
    y_assumption: str,
    output_name: str,
    output_unit: str,
    cells: Sequence[CellInput],
    job_id: uuid.UUID | None = None,
    scenario_id: uuid.UUID | None = None,
) -> Sensitivity:
    """Store a grid and every cell of it.

    Raises:
        ValidationError: If the two axes are the same assumption, if there are no cells, or
            if the grid is not rectangular. The last is worth refusing rather than storing:
            a grid missing a cell renders as a hole, and a reader's first assumption about a
            hole is that the model failed there rather than that nobody computed it.
    """
    if x_assumption == y_assumption:
        message = (
            f"A sensitivity of {x_assumption!r} against itself is a line drawn twice. Only "
            "the diagonal would mean anything and every other cell would contradict it."
        )
        raise ValidationError(message, context={"assumption": x_assumption})

    if not cells:
        message = (
            f"The sensitivity {label!r} has no cells. An empty grid in a report is a heading "
            "promising analysis that is not there."
        )
        raise ValidationError(message, context={"label": label})

    xs = sorted({cell.x_value for cell in cells})
    ys = sorted({cell.y_value for cell in cells})
    if len(cells) != len(xs) * len(ys):
        message = (
            f"The sensitivity {label!r} has {len(cells)} cells for a {len(xs)} by {len(ys)} "
            "grid. A grid with a hole in it renders as a failure at that point rather than "
            "as a point nobody computed."
        )
        raise ValidationError(
            message,
            context={"label": label, "cells": len(cells), "x": len(xs), "y": len(ys)},
        )

    sensitivity = Sensitivity(
        request_id=request_id,
        job_id=job_id,
        scenario_id=scenario_id,
        label=label,
        x_assumption=x_assumption,
        y_assumption=y_assumption,
        output_name=output_name,
        output_unit=output_unit,
    )
    session.add(sensitivity)
    await session.flush()

    for cell in cells:
        session.add(
            SensitivityCell(
                sensitivity_id=sensitivity.id,
                x_value=cell.x_value,
                y_value=cell.y_value,
                output_value=cell.output_value,
                calculation_id=cell.calculation_id,
            )
        )
    await session.flush()

    _log.info(
        "sensitivity.recorded",
        sensitivity_id=str(sensitivity.id),
        label=label,
        x=x_assumption,
        y=y_assumption,
        cells=len(cells),
    )
    return sensitivity


async def sensitivities_for_request(
    session: AsyncSession, request_id: uuid.UUID
) -> list[Sensitivity]:
    rows = await session.scalars(
        select(Sensitivity)
        .where(Sensitivity.request_id == request_id)
        .order_by(Sensitivity.created_at, Sensitivity.label)
    )
    return list(rows)
