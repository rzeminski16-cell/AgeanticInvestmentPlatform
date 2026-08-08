"""The contract as a schema the model can answer, and the guard against it regressing.

**The failure this file exists for.** Every model-written section of the first real report
came back empty and rendered as "This section could not be generated". Nothing was wrong
with the prompt, the evidence, the retry ladder or the validation. The response schema
derived from ``content: dict[str, Any]`` is, on the wire::

    {"type": "object", "properties": {}, "additionalProperties": false}

— an object with no permitted keys. The API enforced it exactly, the model returned ``{}``
every time, and the deterministic check refused the reply for the fields it had been
forbidden to include. Eighteen sections, three attempts each, every run.

Nothing caught it because every test drives a fake provider, and a fake answers from a
script rather than from the schema. So the guard here goes at the schema directly: for
each registered role, put the contract through the SDK's own transformation and refuse a
field that comes out unable to hold anything.
"""

from __future__ import annotations

from typing import Any

import pytest
from anthropic import transform_schema
from pydantic import BaseModel

from aer.agents.base import Agent
from aer.agents.contract_schema import content_model_for, draft_model_for
from aer.agents.custom_section import CustomSectionDraft
from aer.agents.registry import _REGISTRY
from aer.agents.section_writer import SectionDraft, SectionWriterAgent, SectionWriterInput

_EXECUTIVE_SUMMARY: dict[str, Any] = {
    "type": "object",
    "required": ["thesis", "key_points"],
    "properties": {
        "thesis": {"type": "string", "description": "The central view."},
        "key_points": {"type": "array", "items": {"type": "string"}},
        "headline_figures": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["label"],
                "properties": {
                    "label": {"type": "string"},
                    "value": {"type": "string"},
                    "financial_fact_id": {"type": "string"},
                },
            },
        },
    },
}


def _inexpressible(node: Any, path: str) -> list[str]:
    """Object schemas that permit no keys at all: no properties, and no additional ones.

    The exact shape a free-form mapping collapses to once the API's dialect has been
    applied, and the exact shape that silently empties a section.
    """
    found: list[str] = []
    if isinstance(node, dict):
        closed = node.get("additionalProperties") is False
        if node.get("type") == "object" and closed and not node.get("properties"):
            found.append(path)
        for key, value in node.items():
            if isinstance(value, dict):
                found.extend(_inexpressible(value, f"{path}.{key}"))
            elif isinstance(value, list):
                for index, item in enumerate(value):
                    found.extend(_inexpressible(item, f"{path}.{key}[{index}]"))
    return found


def _agents_by_role() -> dict[str, type[Agent[Any, Any]]]:
    found: dict[str, type[Agent[Any, Any]]] = {}
    pending: list[type[Agent[Any, Any]]] = list(Agent.__subclasses__())
    while pending:
        cls = pending.pop()
        pending.extend(cls.__subclasses__())
        role = getattr(cls, "role", None)
        if isinstance(role, str):
            found[role] = cls
    return found


class TestNoRoleAsksForSomethingItCannotReceive:
    """The guard. A declared contract with an unanswerable field must be narrowed per call."""

    @pytest.mark.parametrize("role", sorted(_REGISTRY))
    def test_a_free_form_field_is_narrowed_by_the_agent_that_owns_it(self, role: str) -> None:
        schema = _REGISTRY[role].output_schema()
        empty = _inexpressible(transform_schema(schema), schema.__name__)
        if not empty:
            return

        agent = _agents_by_role().get(role)
        assert agent is not None, f"{role} declares {empty} and has no agent to narrow it"
        assert "response_schema" in vars(agent), (
            f"{role}'s contract cannot express {empty} — the model would be handed a schema "
            f"permitting it to return nothing there. {agent.__name__} must override "
            "response_schema to bind the field at call time."
        )

    def test_the_writers_narrowed_schema_can_hold_its_contract(self) -> None:
        """End to end through the SDK's own transformation, which is where it broke."""
        payload = SectionWriterInput(
            section_key="executive_summary",
            title="Executive Summary",
            company_name="Contoso",
            ticker="CTSO",
            as_of_date="2023-01-01",
            point_in_time=True,
            output_contract=_EXECUTIVE_SUMMARY,
        )
        wire = transform_schema(SectionWriterAgent().response_schema(payload))

        assert _inexpressible(wire, "draft") == []
        content = wire["$defs"]["ExecutiveSummaryContent"]
        assert set(content["properties"]) == {"thesis", "key_points", "headline_figures"}


class TestTheContractBecomesTheSchema:
    def test_declared_fields_are_the_only_fields(self) -> None:
        model = content_model_for(_EXECUTIVE_SUMMARY, name="executive_summary")

        assert set(model.model_fields) == {"thesis", "key_points", "headline_figures"}

    def test_a_required_field_is_required_and_an_optional_one_is_not(self) -> None:
        model = content_model_for(_EXECUTIVE_SUMMARY, name="executive_summary")

        assert model.model_fields["thesis"].is_required()
        assert not model.model_fields["headline_figures"].is_required()

    def test_an_array_of_objects_keeps_its_item_shape(self) -> None:
        """The figure rows are how content cites a stored number. Flattened to strings,
        the renderer's footnotes and the numeral rule both lose their anchor."""
        schema = content_model_for(_EXECUTIVE_SUMMARY, name="executive_summary").model_json_schema()

        rows = [
            defined
            for defined in schema.get("$defs", {}).values()
            if set(defined.get("properties", {})) == {"label", "value", "financial_fact_id"}
        ]
        assert rows, schema

    @pytest.mark.parametrize(
        ("declared", "expected"),
        [("string", str), ("number", float), ("integer", int), ("boolean", bool)],
    )
    def test_each_scalar_maps(self, declared: str, expected: type) -> None:
        contract = {"required": ["x"], "properties": {"x": {"type": declared}}}
        model = content_model_for(contract, name="s")

        assert model.model_fields["x"].annotation is expected

    def test_a_type_this_dialect_does_not_know_becomes_prose(self) -> None:
        """Dropping the field is the one thing that must not happen: a field the model
        cannot return is the whole failure this module was written to end."""
        contract = {"required": ["x"], "properties": {"x": {"type": "geography"}}}
        model = content_model_for(contract, name="s")

        assert model.model_fields["x"].annotation is str

    def test_the_contracts_wording_reaches_the_model(self) -> None:
        """Title and description are what tell the writer what a field is for."""
        model = content_model_for(_EXECUTIVE_SUMMARY, name="executive_summary")

        assert model.model_fields["thesis"].description == "The central view."

    def test_a_field_name_that_is_not_an_identifier_survives_as_itself(self) -> None:
        """Aliased, not renamed. A model whose keys are not the contract's keys is a model
        whose every reply the contract check would reject."""
        contract = {"required": ["key-points"], "properties": {"key-points": {"type": "string"}}}
        model = content_model_for(contract, name="s")

        assert model.model_validate({"key-points": "x"}).model_dump(by_alias=True) == {
            "key-points": "x"
        }

    def test_two_names_that_sanitise_alike_stay_two_fields(self) -> None:
        contract = {"properties": {"a.b": {"type": "string"}, "a-b": {"type": "string"}}}
        model = content_model_for(contract, name="s")

        assert len(model.model_fields) == 2

    def test_a_contract_declaring_nothing_yields_nothing(self) -> None:
        """Honest rather than a fallback to a free-form object: a section declaring no
        fields has none the deterministic check downstream would accept anyway."""
        assert content_model_for({}, name="s").model_fields == {}


class TestTheEnvelopeIsPreserved:
    def test_the_narrowed_draft_is_still_the_role_s_envelope(self) -> None:
        narrowed = draft_model_for(SectionDraft, _EXECUTIVE_SUMMARY, name="executive_summary")

        assert issubclass(narrowed, SectionDraft)
        assert "claims" in narrowed.model_fields

    def test_a_rating_is_still_unrepresentable(self) -> None:
        """``extra="forbid"`` is the §2.12 rating rule at the type level. Narrowing must
        inherit it, or the fix would have opened the thing the envelope exists to close."""
        narrowed = draft_model_for(CustomSectionDraft, _EXECUTIVE_SUMMARY, name="s")

        with pytest.raises(ValueError, match="rating"):
            narrowed.model_validate(
                {"content": {"thesis": "t", "key_points": []}, "claims": [], "rating": "BUY"}
            )

    def test_narrowing_back_returns_the_declared_class_with_a_plain_mapping(self) -> None:
        """Everything downstream — the contract check, the recorders, the renderer — is
        written against ``SectionDraft.content`` being a dict."""
        agent = SectionWriterAgent()
        narrowed = draft_model_for(SectionDraft, _EXECUTIVE_SUMMARY, name="executive_summary")
        reply: BaseModel = narrowed.model_validate(
            {"content": {"thesis": "A view.", "key_points": ["one"]}, "claims": []}
        )

        declared = agent._as_declared(reply)

        assert type(declared) is SectionDraft
        assert declared.content == {"thesis": "A view.", "key_points": ["one"]}

    def test_a_field_name_that_is_not_an_identifier_comes_back_as_itself(self) -> None:
        """The attribute is sanitised; the content key must not be. A dump that lost the
        aliases would hand the contract check ``key_points`` where it declared
        ``key-points`` — a missing required field and an undeclared one, from one slip."""
        agent = SectionWriterAgent()
        contract = {"required": ["key-points"], "properties": {"key-points": {"type": "string"}}}
        narrowed = draft_model_for(SectionDraft, contract, name="s")
        reply: BaseModel = narrowed.model_validate({"content": {"key-points": "one"}, "claims": []})

        assert agent._as_declared(reply).content == {"key-points": "one"}

    def test_an_omitted_optional_field_is_absent_rather_than_null(self) -> None:
        """A declared field holding null passes "is it there?" and fails every reader
        after that — including the renderer, which would print an empty heading."""
        agent = SectionWriterAgent()
        narrowed = draft_model_for(SectionDraft, _EXECUTIVE_SUMMARY, name="executive_summary")
        reply: BaseModel = narrowed.model_validate(
            {"content": {"thesis": "A view.", "key_points": ["one"]}, "claims": []}
        )

        assert "headline_figures" not in agent._as_declared(reply).content
