"""Turning an HTML form submission into a validated request payload.

An HTML form is a flat mapping of strings. The schema is nested, typed and strict. This
module is the adapter between them, and it is deliberately the *only* thing that adapts —
it does not decide whether anything is valid. Every rule stays in
:mod:`aer.core.schemas.request` and :mod:`aer.services.requests`, so the form and the
JSON API cannot come to different conclusions about the same submission.

Two conversions happen here and nowhere else:

* **Percentages to fractions.** The form asks for portfolio weights in per cent because
  that is how people say them; the schema and the database store fractions. Asking an
  operator to type ``0.025`` invites someone to type ``2.5`` and silently commission
  research against a 250% position.
* **Textareas to lists.** Focus questions and excluded sources are one per line, which is
  the natural HTML representation of a short list.

On failure the operator's input is handed back verbatim so the form can be re-rendered
with it. Losing a carefully written set of focus questions to a mistyped ticker is the
kind of thing that stops people using a tool.

**One known limit, stated rather than hidden.** Errors arrive in two rounds, not one.
Every *schema* problem is reported together, and every *service* problem — a future
as-of date, an over-budget ceiling, a universe exclusion — is reported together. But the
service rules cannot run on a payload that failed to construct, so a submission with both
kinds shows the schema problems first and the rest on resubmission. Closing that would
mean running domain rules over half-parsed input, which trades a rare second round trip
for a permanent source of rules evaluated against values that were never valid.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any, Final

from pydantic import ValidationError as PydanticValidationError

from aer.core.schemas.request import FieldProblem, ResearchRequestCreate

if TYPE_CHECKING:
    from aer.db.models import ResearchRequest

__all__ = [
    "FORM_FIELDS",
    "ParsedForm",
    "form_values_from",
    "fraction_to_percent",
    "parse_request_form",
    "percent_to_fraction",
]

# Every input the form renders, in the order it appears. Used to echo values back on a
# failed submission and asserted against in the tests, so a field added to the template
# without being handled here is caught rather than silently ignored.
FORM_FIELDS: Final[tuple[str, ...]] = (
    "company_name",
    "ticker",
    "exchange",
    "isin",
    "as_of_date",
    "base_currency",
    "reporting_currency",
    "investment_horizon_months",
    "horizon_label",
    "analysis_mode",
    "point_in_time",
    "current_weight_percent",
    "maximum_weight_percent",
    "benchmark",
    "risk_tolerance",
    "liquidity_constraint_gbp",
    "esg_sensitivity",
    "focus_questions",
    "excluded_sources",
    "max_cost_gbp",
)

# Where a schema error lands on the page. Pydantic reports a nested location such as
# ("portfolio_context", "current_weight"); the form input is called something else, and
# an error rendered next to no input at all is an error the operator cannot act on.
_LOCATION_TO_FIELD: Final[dict[tuple[str, ...], str]] = {
    ("portfolio_context", "current_weight"): "current_weight_percent",
    ("portfolio_context", "maximum_weight"): "maximum_weight_percent",
    ("portfolio_context", "benchmark"): "benchmark",
    ("portfolio_context",): "current_weight_percent",
}

_HUNDRED: Final = Decimal(100)


@dataclass(slots=True)
class ParsedForm:
    """The outcome of a submission: a payload, or errors plus what was typed."""

    payload: ResearchRequestCreate | None
    errors: dict[str, list[str]] = field(default_factory=dict)
    values: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.payload is not None and not self.errors

    def add_problems(self, problems: list[FieldProblem]) -> None:
        """Merge service-layer rejections into the same shape the template renders.

        Contextual rules and universe exclusions are checked after the schema, so their
        messages arrive separately — but to the operator they are one list of things
        wrong with one submission, and that is how they must appear.
        """
        for problem in problems:
            self.errors.setdefault(_form_field_for(problem.field), []).append(problem.message)


def percent_to_fraction(raw: str) -> Decimal | None:
    """Convert a percentage typed by a person into the fraction the schema stores.

    Returns ``None`` for a blank input, which means "not specified" rather than zero: a
    holding of zero and no view on the holding are different statements.

    Raises:
        ValueError: If the text is not a number. Raised rather than silently returning
            ``None``, because a typo becoming "unspecified" is how a weight quietly
            disappears from a request.
    """
    text = raw.strip().rstrip("%").strip()
    if not text:
        return None
    try:
        return Decimal(text) / _HUNDRED
    except InvalidOperation as exc:
        message = "must be a number, for example 2.5 for two and a half per cent"
        raise ValueError(message) from exc


def fraction_to_percent(value: Decimal | None) -> str:
    """Render a stored fraction back into the form's percentage input."""
    if value is None:
        return ""
    # normalize() strips trailing zeros so 0.0250 renders as "2.5" rather than "2.500".
    # The ":f" is not decoration: normalize() turns a whole hundred into Decimal("1E+2"),
    # and "1E+2" in a number input is a value the browser will not accept and the operator
    # cannot read. Fixed-point formatting is the only way back to "100".
    return f"{(value * _HUNDRED).normalize():f}"


def _lines(raw: str) -> list[str]:
    return [line.strip() for line in raw.splitlines() if line.strip()]


def _form_field_for(location: str) -> str:
    """Map a schema field name onto the form input that carries it."""
    return _LOCATION_TO_FIELD.get((location,), location)


def parse_request_form(form: dict[str, str]) -> ParsedForm:
    """Build a :class:`ResearchRequestCreate` from raw form values.

    Reports every problem it can rather than stopping at the first, matching how the rest
    of the platform reports configuration and validation failures.
    """
    values = {name: form.get(name, "") for name in FORM_FIELDS}
    errors: dict[str, list[str]] = {}

    weights: dict[str, Decimal | None] = {}
    for form_name, schema_name in (
        ("current_weight_percent", "current_weight"),
        ("maximum_weight_percent", "maximum_weight"),
    ):
        try:
            weights[schema_name] = percent_to_fraction(values[form_name])
        except ValueError as exc:
            errors.setdefault(form_name, []).append(str(exc))

    if errors:
        # Without a usable weight the schema cannot be constructed at all, so report what
        # is known now rather than adding a spurious "input should be a valid decimal" on
        # top of the message that already says what is wrong.
        return ParsedForm(payload=None, errors=errors, values=values)

    payload: dict[str, Any] = {
        "company_name": values["company_name"],
        "ticker": values["ticker"],
        "exchange": values["exchange"],
        "isin": values["isin"],
        "as_of_date": values["as_of_date"],
        "base_currency": values["base_currency"],
        "reporting_currency": values["reporting_currency"],
        "investment_horizon_months": values["investment_horizon_months"],
        "horizon_label": values["horizon_label"],
        "analysis_mode": values["analysis_mode"],
        # An unchecked checkbox is simply absent from the submission -- there is no
        # "false" to read, only a missing key.
        "point_in_time": values["point_in_time"] != "",
        "portfolio_context": {
            "current_weight": weights.get("current_weight"),
            "maximum_weight": weights.get("maximum_weight"),
            "benchmark": values["benchmark"],
        },
        "risk_tolerance": values["risk_tolerance"],
        "liquidity_constraint_gbp": values["liquidity_constraint_gbp"] or None,
        "esg_sensitivity": values["esg_sensitivity"],
        "focus_questions": _lines(values["focus_questions"]),
        "excluded_sources": _lines(values["excluded_sources"]),
        "max_cost_gbp": values["max_cost_gbp"],
    }

    try:
        return ParsedForm(payload=ResearchRequestCreate(**payload), values=values)
    except PydanticValidationError as exc:
        for detail in exc.errors():
            location = tuple(str(part) for part in detail["loc"])
            form_field = _LOCATION_TO_FIELD.get(location) or _form_field_for(
                location[0] if location else ""
            )
            errors.setdefault(form_field, []).append(_readable(detail))
        return ParsedForm(payload=None, errors=errors, values=values)


def form_values_from(request: ResearchRequest) -> dict[str, str]:
    """Render a stored request back into the flat strings the form template reads.

    The exact inverse of :func:`parse_request_form`, and it has to stay that way: a value
    this function renders differently from the way the parser reads it produces an edit
    form that changes a field nobody touched. The round trip is asserted in the tests
    rather than left to inspection.

    Every value is a string, including the ones that are not text. That is what an HTML
    form is, and converting here rather than in the template keeps the "" that means
    "unset" in one place instead of scattered through Jinja conditionals.
    """
    portfolio = request.portfolio_context or {}
    return {
        "company_name": request.company_name,
        "ticker": request.ticker,
        "exchange": request.exchange,
        "isin": request.isin or "",
        "as_of_date": request.as_of_date.isoformat(),
        "base_currency": request.base_currency,
        "reporting_currency": request.reporting_currency or "",
        "investment_horizon_months": str(request.investment_horizon_months),
        "horizon_label": request.horizon_label or "",
        "analysis_mode": request.analysis_mode.value,
        # An unchecked checkbox submits nothing at all, so "" is the only honest
        # representation of false here -- the parser reads presence, not a value.
        "point_in_time": "true" if request.point_in_time else "",
        "current_weight_percent": fraction_to_percent(_weight(portfolio.get("current_weight"))),
        "maximum_weight_percent": fraction_to_percent(_weight(portfolio.get("maximum_weight"))),
        "benchmark": str(portfolio.get("benchmark") or ""),
        "risk_tolerance": request.risk_tolerance or "",
        "liquidity_constraint_gbp": _plain(request.liquidity_constraint_gbp),
        "esg_sensitivity": request.esg_sensitivity or "",
        "focus_questions": "\n".join(request.focus_questions or ()),
        "excluded_sources": "\n".join(request.excluded_sources or ()),
        "max_cost_gbp": _plain(request.max_cost_gbp),
    }


def _weight(raw: Any) -> Decimal | None:
    """Read a portfolio weight back out of JSONB.

    Stored as a JSON *string* by ``model_dump(mode="json")`` precisely so no float is ever
    involved. ``str()`` before ``Decimal()`` covers the case where something else wrote a
    number, which would otherwise raise here rather than in whatever wrote it.
    """
    if raw is None:
        return None
    try:
        return Decimal(str(raw))
    except InvalidOperation:
        return None


def _plain(value: Decimal | None) -> str:
    return "" if value is None else str(value)


def _readable(detail: Mapping[str, Any]) -> str:
    """A message for a person, without echoing what they submitted.

    Pydantic's own message is usually fine, but its ``input`` and ``ctx`` are not: they
    reflect the submitted value straight back onto the page, and a credential pasted into
    the wrong box would be reflected with it.
    """
    message = str(detail.get("msg", "is invalid"))
    return message.removeprefix("Value error, ")
