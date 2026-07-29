"""What makes editing a request safe, tested without a database.

Two properties, both of which fail silently rather than loudly if they break.

1. **The form can render exactly what it parses.** ``form_values_from`` is the inverse of
   ``parse_request_form``. If one renders a value the other reads differently, opening the
   edit form and pressing save changes a field nobody touched — a weight divided by a
   hundred, a currency blanked — and nothing anywhere reports an error.
2. **The audit diff covers every field the writer writes.** ``_apply`` assigns the columns;
   ``_EDITABLE_FIELDS`` is what the edit's audit entry diffs over. A field in one and not
   the other is a field that can be changed with no record that it changed.

The second is checked structurally, against ``_apply``'s own source, because the failure
mode is a field *added later* — which no example-based test would ever notice.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
import uuid
from datetime import date
from decimal import Decimal

import pytest

from aer.core.enums import AnalysisMode
from aer.db.models import ResearchRequest
from aer.services import requests as request_service
from aer.web.forms import (
    FORM_FIELDS,
    form_values_from,
    fraction_to_percent,
    parse_request_form,
    percent_to_fraction,
)


def a_request(**overrides: object) -> ResearchRequest:
    """A populated request row, never persisted. Nothing here touches the database."""
    values: dict[str, object] = {
        "id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "company_name": "Microsoft Corporation",
        "ticker": "MSFT",
        "exchange": "NASDAQ",
        "isin": "US5949181045",
        "as_of_date": date(2022, 6, 30),
        "base_currency": "USD",
        "reporting_currency": "GBP",
        "investment_horizon_months": 36,
        "horizon_label": "Through the next capex cycle",
        "analysis_mode": AnalysisMode.FULL,
        "point_in_time": True,
        # As stored: `model_dump(mode="json")` writes the weights as strings so that no
        # float is ever involved on the way into JSONB.
        "portfolio_context": {
            "current_weight": "0.025",
            "maximum_weight": "0.05",
            "benchmark": "MSCI World",
        },
        "risk_tolerance": "balanced",
        "liquidity_constraint_gbp": Decimal("250000.00"),
        "esg_sensitivity": "considered",
        "focus_questions": ["How durable is the Azure gross margin?", "What breaks the bull?"],
        "excluded_sources": ["seekingalpha.com"],
        "max_cost_gbp": Decimal("2.50"),
    }
    values.update(overrides)
    return ResearchRequest(**values)


def _fields_assigned_by_apply() -> set[str]:
    """Every ``request.<name> = ...`` in :func:`_apply`, read from its source.

    Structural rather than behavioural on purpose. The failure this guards against is a
    field added to ``_apply`` and forgotten in ``_EDITABLE_FIELDS`` — which is invisible to
    any test written against the fields that exist today.
    """
    source = textwrap.dedent(inspect.getsource(request_service._apply))
    assigned: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "request"
            ):
                assigned.add(target.attr)
    return assigned


class TestTheAuditDiffCoversEveryEditableField:
    def test_apply_writes_exactly_the_declared_fields(self) -> None:
        assert _fields_assigned_by_apply() == set(request_service._EDITABLE_FIELDS)

    def test_the_scan_finds_something(self) -> None:
        # Without this, a scan that silently matched nothing would make the test above pass
        # against an empty set and prove the opposite of what it claims.
        assert len(_fields_assigned_by_apply()) > 10

    def test_every_declared_field_is_a_real_column(self) -> None:
        for name in request_service._EDITABLE_FIELDS:
            assert hasattr(ResearchRequest, name), name


class TestTheFormRoundTrip:
    def test_a_stored_request_re_parses_to_the_same_payload(self) -> None:
        stored = a_request()

        parsed = parse_request_form(form_values_from(stored))

        assert parsed.payload is not None, parsed.errors
        payload = parsed.payload
        assert payload.company_name == stored.company_name
        assert payload.ticker == stored.ticker
        assert payload.exchange == stored.exchange
        assert payload.isin == stored.isin
        assert payload.as_of_date == stored.as_of_date
        assert payload.base_currency == stored.base_currency
        assert payload.reporting_currency == stored.reporting_currency
        assert payload.investment_horizon_months == stored.investment_horizon_months
        assert payload.horizon_label == stored.horizon_label
        assert payload.analysis_mode is stored.analysis_mode
        assert payload.point_in_time == stored.point_in_time
        assert payload.focus_questions == stored.focus_questions
        assert payload.excluded_sources == stored.excluded_sources
        assert payload.max_cost_gbp == stored.max_cost_gbp
        assert payload.liquidity_constraint_gbp == stored.liquidity_constraint_gbp
        assert payload.risk_tolerance is not None
        assert payload.risk_tolerance.value == stored.risk_tolerance
        assert payload.esg_sensitivity is not None
        assert payload.esg_sensitivity.value == stored.esg_sensitivity

    def test_the_weights_survive_the_round_trip_exactly(self) -> None:
        # The one conversion in the whole adapter, and the one most likely to lose a factor
        # of a hundred without anything erroring.
        parsed = parse_request_form(form_values_from(a_request()))

        assert parsed.payload is not None
        assert parsed.payload.portfolio_context.current_weight == Decimal("0.025")
        assert parsed.payload.portfolio_context.maximum_weight == Decimal("0.05")
        assert parsed.payload.portfolio_context.benchmark == "MSCI World"

    def test_an_unset_optional_renders_as_blank_and_reads_back_as_none(self) -> None:
        stored = a_request(
            isin=None,
            reporting_currency=None,
            horizon_label=None,
            risk_tolerance=None,
            esg_sensitivity=None,
            liquidity_constraint_gbp=None,
            portfolio_context={},
            focus_questions=[],
            excluded_sources=[],
        )

        values = form_values_from(stored)
        parsed = parse_request_form(values)

        assert values["isin"] == ""
        assert parsed.payload is not None, parsed.errors
        assert parsed.payload.isin is None
        assert parsed.payload.reporting_currency is None
        assert parsed.payload.risk_tolerance is None
        assert parsed.payload.liquidity_constraint_gbp is None
        assert parsed.payload.portfolio_context.is_empty()

    def test_point_in_time_off_renders_as_an_absent_checkbox(self) -> None:
        # An unchecked box submits nothing at all, so "" is the only honest representation.
        # Rendering "false" would read back as *present*, and silently turn the guard on.
        values = form_values_from(a_request(point_in_time=False))

        assert values["point_in_time"] == ""
        parsed = parse_request_form(values)
        assert parsed.payload is not None
        assert parsed.payload.point_in_time is False

    def test_it_fills_every_field_the_form_renders(self) -> None:
        assert set(form_values_from(a_request())) == set(FORM_FIELDS)

    def test_every_value_is_a_string(self) -> None:
        # An HTML form is strings. A stray Decimal or date here renders through Jinja by
        # accident and reads back differently on a platform with a different repr.
        for name, value in form_values_from(a_request()).items():
            assert isinstance(value, str), name


class TestPercentConversion:
    @pytest.mark.parametrize(
        ("fraction", "rendered"),
        [
            (Decimal("0.025"), "2.5"),
            (Decimal("0.0250"), "2.5"),
            (Decimal("0.05"), "5"),
            (Decimal(0), "0"),
            # normalize() turns a whole hundred into Decimal("1E+2"). "1E+2" in a number
            # input is a value the browser will not accept and the operator cannot read.
            (Decimal(1), "100"),
            (Decimal("0.1"), "10"),
        ],
    )
    def test_a_fraction_renders_as_a_readable_percentage(
        self, fraction: Decimal, rendered: str
    ) -> None:
        assert fraction_to_percent(fraction) == rendered

    @pytest.mark.parametrize(
        "fraction", [Decimal("0.025"), Decimal("0.05"), Decimal(1), Decimal(0)]
    )
    def test_rendering_then_parsing_is_the_identity(self, fraction: Decimal) -> None:
        assert percent_to_fraction(fraction_to_percent(fraction)) == fraction

    def test_an_unset_weight_stays_unset(self) -> None:
        # Not zero. "I hold none of this" and "I have no view on the holding" are different
        # statements, and only one of them is a number.
        assert fraction_to_percent(None) == ""
        assert percent_to_fraction("") is None

    def test_a_typo_is_reported_rather_than_silently_dropped(self) -> None:
        with pytest.raises(ValueError, match="must be a number"):
            percent_to_fraction("two and a half")
