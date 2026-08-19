"""The valuation section's method block, rendered from the run's own records.

**A claim about how a number was produced is a claim about a calculation** (ADR 0063).
The first complete report's DCF section described beta regressions, bond-yield curves and
market-value weights — a methodology the run never executed — and every existing defence
passed it: the numeral rule guards figures and the section stated almost none; citation
verification guards quotes and these were not quotes. A section can evade the whole
validation apparatus by being confidently qualitative, and prose about method is exactly
where a reader's trust is set.

So the method stops being model output. :func:`valuation_method_block` renders it from
the calculation ledger and the confirmed assumptions: every cost-of-capital component
with its value and *how it was set* — measured from filings, or typed by a person — the
forecast drivers named as the assumptions they are, both terminal methods with the
recorded caveats between them, and the share count with its source. The model keeps a
commentary field, and :func:`commentary_problems` is the deterministic edge of its
policy: a commentary that names a method input the calculation store does not contain is
refused, because the only true statements about this valuation's inputs are the ones the
record can back.
"""

from __future__ import annotations

import re
import uuid
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from aer.db.models import Assumption, Calculation, JobStep, ResearchRequest

__all__ = ["commentary_problems", "valuation_method_block"]

# The workflow step whose recorded output says whether a valuation ran and what caveats it
# carried. The literal is duplicated from ``vertical_slice_v1.VALUE_STEP`` because the
# workflow imports this package and the dependency cannot point back.
_VALUE_STEP: Final = "value"

# The scalar assumptions the discount rate is decomposed from, with the labels a reader
# sees. Order is presentation order.
_WACC_ASSUMPTIONS: Final[tuple[tuple[str, str], ...]] = (
    ("risk_free_rate", "Risk-free rate"),
    ("beta", "Beta"),
    ("equity_risk_premium", "Equity risk premium"),
    ("tax_rate", "Tax rate"),
)

# The cost-of-capital calculations, in the order the rate is assembled. A run with no debt
# records no debt rows; absent names are simply not rendered.
_WACC_CALCULATIONS: Final[tuple[tuple[str, str], ...]] = (
    ("cost_of_equity", "Cost of equity"),
    ("cost_of_debt", "Cost of debt, pre-tax"),
    ("after_tax_cost_of_debt", "Cost of debt, after tax"),
    ("equity_weight", "Equity weight"),
    ("debt_weight", "Debt weight"),
    ("wacc", "WACC"),
    ("wacc_all_equity", "WACC"),
)

# The forecast assumptions a reader should see named as assumptions: the six drivers and
# the two terminal choices. Per-year driver paths are stored as ``name_year_N``.
_FORECAST_ASSUMPTIONS: Final[tuple[tuple[str, str], ...]] = (
    ("revenue_growth", "Revenue growth"),
    ("ebit_margin", "EBIT margin"),
    ("capex_intensity", "Capex intensity"),
    ("depreciation_intensity", "Depreciation intensity"),
    ("working_capital_intensity", "Working capital intensity"),
    ("terminal_growth", "Terminal growth"),
    ("exit_multiple", "Exit multiple"),
)

_METHOD_LABELS: Final[tuple[tuple[str, str], ...]] = (
    ("gordon_growth", "Gordon growth"),
    ("exit_multiple", "Exit multiple"),
)


async def valuation_method_block(
    session: AsyncSession, *, job_id: uuid.UUID, request: ResearchRequest
) -> dict[str, Any]:
    """The platform-rendered fields of the valuation section, from the ledger.

    Everything here is read back from ``calculations``, ``assumptions`` and the value
    step's recorded output — never recomputed — so the block describes the valuation the
    run actually performed, in the only sense that survives an audit. A run that produced
    no valuation gets the honest one-line state and nothing that looks like a method.
    """
    produced = await _value_step_output(session, job_id)
    if not produced.get("valued"):
        reason = str(produced.get("reason") or "the run recorded no valuation step output")
        return {
            "method_note": (
                "No discounted cash flow was produced for this run, so there is no method "
                f"to describe: {reason}"
            )
        }

    calculations = list(
        await session.scalars(
            select(Calculation)
            .where(Calculation.job_id == job_id)
            .order_by(Calculation.created_at, Calculation.sequence)
        )
    )
    assumptions = {
        row.name: row
        for row in await session.scalars(
            select(Assumption)
            .where(Assumption.request_id == request.id, Assumption.approved.is_(True))
            .options(selectinload(Assumption.proposals))
        )
    }

    basis = str(produced.get("equity_basis") or "")
    block: dict[str, Any] = {
        "method_note": _method_note(produced),
        "cost_of_capital": _cost_of_capital_rows(calculations, assumptions, basis=basis),
        "forecast_drivers": _forecast_rows(assumptions),
        "terminal_valuations": _terminal_rows(calculations),
    }
    caveats = [str(item) for item in produced.get("caveats") or [] if str(item).strip()]
    if caveats:
        block["valuation_caveats"] = caveats
    return {key: value for key, value in block.items() if value}


async def _value_step_output(session: AsyncSession, job_id: uuid.UUID) -> dict[str, Any]:
    step = await session.scalar(
        select(JobStep).where(JobStep.job_id == job_id, JobStep.step_key == _VALUE_STEP)
    )
    if step is None or not isinstance(step.output_ref, dict):
        return {}
    return step.output_ref


def _method_note(produced: dict[str, Any]) -> str:
    """What was actually run, in one paragraph a model never writes.

    Assembled from the step's recorded output alone. The years figure is a structural
    parameter of the forecast, not a measurement, which is why stating it here does not
    need a calculation row — the same footing as "the bridge is net debt alone".
    """
    years = produced.get("years")
    horizon = f"a {years}-year explicit forecast" if years else "an explicit forecast"
    weights = (
        "book values from the filed balance sheet — this run holds no market prices"
        if str(produced.get("equity_basis")) == "book"
        else "the recorded capital-structure values"
    )
    return (
        "The figures in this section were produced by the platform's recorded arithmetic, "
        f"and this description is rendered from that record. Free cash flows from {horizon} "
        "of consolidated figures were discounted at the weighted average cost of capital "
        f"shown below, with the capital weighted at {weights}. The bridge from enterprise "
        "to equity value is net debt alone, and the terminal value was taken both ways — a "
        "growing perpetuity and an exit multiple — with each carried through to a per-share "
        "figure. Every figure cites the calculation that produced it."
    )


def _cost_of_capital_rows(
    calculations: list[Calculation],
    assumptions: dict[str, Assumption],
    *,
    basis: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for name, label in _WACC_ASSUMPTIONS:
        found = assumptions.get(name)
        if found is not None:
            rows.append(_assumption_row(found, label=label))

    for name, label in _WACC_CALCULATIONS:
        calculation = _base_case(calculations, name=name)
        if calculation is None:
            continue
        provenance = f"computed: {calculation.formula}"
        if name in {"equity_weight", "debt_weight"} and basis == "book":
            provenance += " (book values — this run holds no market prices)"
        rows.append(_calculation_row(calculation, label=label, provenance=provenance))
    return rows


def _forecast_rows(assumptions: dict[str, Assumption]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for name, label in _FORECAST_ASSUMPTIONS:
        flat = assumptions.get(name)
        if flat is not None:
            rows.append(_assumption_row(flat, label=label))
            continue
        # A per-year path: one row per year, so each value keeps its own provenance and
        # nothing joins figures into a string the formatter cannot read.
        for year in range(1, 21):
            yearly = assumptions.get(f"{name}_year_{year}")
            if yearly is None:
                break
            rows.append(_assumption_row(yearly, label=f"{label} — year {year}"))
    return rows


def _terminal_rows(calculations: list[Calculation]) -> list[dict[str, Any]]:
    """Both terminal methods, each carried to its per-share figure, plus the share count.

    The distance between the two methods is not restated as a new number — a figure needs
    a recorded calculation, and the comparison already exists as words in the valuation's
    recorded caveats, which render below this table.
    """
    rows: list[dict[str, Any]] = []
    for method, method_label in _METHOD_LABELS:
        per_share = _base_case(calculations, name="value_per_share", method=method)
        share = _base_case(calculations, name="terminal_value_share", method=method)
        if per_share is not None:
            rows.append(
                _calculation_row(
                    per_share,
                    label=f"Value per share — {method_label}",
                    provenance=f"computed: {per_share.formula}",
                )
            )
        if share is not None:
            rows.append(
                _calculation_row(
                    share,
                    label=f"Terminal value share — {method_label}",
                    provenance=f"computed: {share.formula}",
                )
            )

    shares = _shares_input(calculations)
    if shares is not None:
        rows.append(shares)
    return rows


def _shares_input(calculations: list[Calculation]) -> dict[str, Any] | None:
    """The share count the per-share figures divide by, from the calculation's own inputs."""
    per_share = _base_case(calculations, name="value_per_share")
    if per_share is None:
        return None
    for entry in per_share.inputs:
        if str(entry.get("name")) != "shares":
            continue
        source = entry.get("source") or {}
        label = str(source.get("label") or "").strip()
        return {
            "label": "Shares outstanding",
            "value": str(entry.get("value", "")),
            "unit": str(entry.get("unit", "")),
            "provenance": f"the filed count: {label}" if label else "the filed count",
            "calculation_id": str(per_share.id),
        }
    return None


def _assumption_row(assumption: Assumption, *, label: str) -> dict[str, Any]:
    """One confirmed assumption, saying plainly whether a person typed it.

    The latest proposal is the one whose value stands, and its ``by_human`` is the fact of
    the matter — stored when it happened rather than inferred from the proposer string. A
    value a person typed says "set by the operator" and never anything that could read as
    a measurement.
    """
    latest = assumption.proposals[-1] if assumption.proposals else None
    if latest is not None and latest.by_human:
        provenance = "set by the operator and confirmed at the assumptions gate"
    elif latest is not None:
        provenance = (
            f"proposed by {latest.proposed_by} with a stated justification, and confirmed "
            "by the operator at the assumptions gate"
        )
    else:  # pragma: no cover -- every confirmed assumption has at least its first proposal
        provenance = "confirmed at the assumptions gate"
    return {
        "label": label,
        "value": str(assumption.value),
        "unit": assumption.unit,
        "provenance": provenance,
    }


def _calculation_row(calculation: Calculation, *, label: str, provenance: str) -> dict[str, Any]:
    return {
        "label": label,
        "value": str(calculation.output_value),
        "unit": calculation.output_unit,
        "provenance": provenance,
        "calculation_id": str(calculation.id),
    }


def _base_case(
    calculations: list[Calculation], *, name: str, method: str | None = None
) -> Calculation | None:
    """The base valuation's calculation of this name — the first base-case match.

    The *first*, deliberately, where the valuation surface reads the last: a sensitivity
    grid's cells are whole discounted cash flows recorded after the base run, each stamped
    ``case="base"`` because a grid varies inputs rather than naming a scenario — so the
    tail of the ledger is a grid corner, and the head of it is the valuation the report
    describes. Scenario rows carry their own case names and are excluded either way.
    """
    matching = [
        row
        for row in calculations
        if row.name == name
        and str(row.parameters.get("case", "base")) == "base"
        and (method is None or str(row.parameters.get("method", "")) == method)
    ]
    return matching[0] if matching else None


# -- The commentary's deterministic edge -----------------------------------------------------

# Method inputs this workflow never holds, whatever the run: market prices, traded debt,
# return regressions. A commentary reaching for one is describing work that did not happen.
_NEVER_HELD: Final[tuple[str, ...]] = (
    r"share price",
    r"closing price",
    r"market price",
    r"market capitalisation",
    r"market capitalization",
    r"market[- ]value weights?",
    r"market weights?",
    r"traded yields?",
    r"bond yields?",
    r"note coupons?",
    r"bond coupons?",
    r"(?:weekly|monthly|daily) returns",
    r"regression",
)

# Method-input terms mapped to the label that must exist in the rendered block for the
# term to be in bounds. Matched on word boundaries, case-insensitively.
_COMPONENT_TERMS: Final[tuple[tuple[str, str], ...]] = (
    (r"beta", "beta"),
    (r"risk[- ]free", "risk-free rate"),
    (r"(?:equity|market) risk premium", "equity risk premium"),
    (r"cost of debt", "cost of debt"),
    (r"cost of equity", "cost of equity"),
    (r"tax rate", "tax rate"),
    (r"terminal growth", "terminal growth"),
    (r"exit multiple", "exit multiple"),
    (r"wacc|discount rate", "wacc"),
)


def commentary_problems(content: dict[str, Any], block: dict[str, Any]) -> list[str]:
    """What the commentary says about the method that the record cannot back.

    The deterministic edge of the section's policy: interpretation is the model's, the
    method is the platform's, and the boundary is enforced by refusing a commentary that
    names an input absent from the rendered block. Each problem names the term so a retry
    can remove it rather than guess.
    """
    commentary = str(content.get("commentary") or "")
    if not commentary:
        return []

    problems = [
        f"The commentary mentions {_found(commentary, pattern)!r}, and this run holds no "
        "such input — no prices, no traded debt, no return series. The method is rendered "
        "above the commentary from the run's own records; interpret the figures instead of "
        "describing how they might have been produced."
        for pattern in _NEVER_HELD
        if _found(commentary, pattern)
    ]

    labels = " | ".join(_block_labels(block)).lower()
    problems.extend(
        f"The commentary mentions {_found(commentary, pattern)!r}, and the run's "
        "calculation store contains no such component. Only inputs the rendered method "
        "block states may be discussed."
        for pattern, required in _COMPONENT_TERMS
        if _found(commentary, pattern) and required not in labels
    )
    return problems


def _found(text: str, pattern: str) -> str:
    match = re.search(rf"\b(?:{pattern})\b", text, flags=re.IGNORECASE)
    return match.group(0) if match else ""


def _block_labels(block: dict[str, Any]) -> list[str]:
    labels: list[str] = []
    for field in ("cost_of_capital", "forecast_drivers", "terminal_valuations"):
        for row in block.get(field) or []:
            if isinstance(row, dict):
                labels.append(str(row.get("label", "")))
    return labels
