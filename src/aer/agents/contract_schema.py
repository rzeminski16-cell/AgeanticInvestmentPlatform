"""A section's output contract, as a schema the model can actually answer.

**This module exists because of a report with eighteen empty sections.** A section writer
returns ``content: dict[str, Any]``, meaning "an object shaped by this section's contract",
and the contract was passed to the model as *text* to follow. But the API's structured
output enforces a schema, and the schema derived from ``dict[str, Any]`` is::

    {"type": "object", "properties": {}, "additionalProperties": false}

An object with no declared properties and no additional ones permitted: an object that may
only ever be ``{}``. The closure is not a bug in the SDK — the API's JSON-schema mode
requires ``additionalProperties: false`` on every object, so a free-form mapping is simply
not expressible. Every section the platform has ever written came back empty, was refused
for the fields it could not have contained, and rendered as "This section could not be
generated". The prompt asked for a thesis; the schema forbade one.

So the contract stops being advice and becomes the schema. Each call builds a Pydantic
model from the section's own ``output_contract`` and asks for *that*, which makes the
declared fields the only fields — expressible, required where the contract says required,
and closed against everything else exactly as the deterministic check downstream is.

The dialect handled here is the one contracts are written in: objects, arrays, strings,
numbers, integers and booleans, nested. Anything unrecognised becomes a string, because a
field the model can describe in words is worth more than a field it cannot return at all.
"""

from __future__ import annotations

import keyword
import re
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field, create_model

__all__ = ["CONTENT_FIELD", "content_model_for", "draft_model_for"]

# The envelope field holding the section's own output. Named once because two agents and
# their tests all mean this one field.
CONTENT_FIELD: Final = "content"

# JSON Schema's scalar names, as Python types. ``integer`` before ``number`` matters only
# to a reader: both are accepted, and a contract asking for one gets it.
_SCALARS: Final[dict[str, type]] = {
    "string": str,
    "number": float,
    "integer": int,
    "boolean": bool,
}

# What a property becomes when the contract does not say, or says something this dialect
# does not know. Prose is the safe default: every renderer can show a string, and the
# alternative — dropping the field — is the failure this module was written to end.
_UNKNOWN: Final[type] = str

_NON_IDENTIFIER: Final[re.Pattern[str]] = re.compile(r"\W|^(?=\d)")


def content_model_for(contract: dict[str, Any], *, name: str) -> type[BaseModel]:
    """The contract as a Pydantic model, for one section's generation call.

    Args:
        contract: The section's ``output_contract``: a JSON Schema object with
            ``properties`` and, usually, ``required``.
        name: Something identifying the section, used to name the generated class. Only
            legibility depends on it — in a log line, in a schema title.

    A contract declaring no properties yields a model with no fields. That is the honest
    answer rather than a fallback to a free-form object: a section that declares nothing
    has nothing the platform would accept, and the deterministic contract check downstream
    refuses undeclared keys anyway.
    """
    return _object_model(contract, name=f"{_class_name(name)}Content")


def draft_model_for(
    envelope: type[BaseModel], contract: dict[str, Any], *, name: str
) -> type[BaseModel]:
    """A section-draft envelope whose ``content`` is this section's contract.

    A subclass of the declared envelope, so the claims field, the ``extra="forbid"`` rule
    and everything else the contract says about a draft carry over untouched and only the
    one unexpressible field is narrowed.
    """
    narrowed: type[BaseModel] = create_model(
        f"{_class_name(name)}{envelope.__name__}",
        __base__=envelope,
        **{CONTENT_FIELD: (content_model_for(contract, name=name), ...)},  # type: ignore[call-overload]
    )
    return narrowed


def _object_model(schema: dict[str, Any], *, name: str) -> type[BaseModel]:
    properties = schema.get("properties")
    declared: dict[str, Any] = properties if isinstance(properties, dict) else {}
    required = schema.get("required")
    needed = {str(item) for item in required} if isinstance(required, list) else set()

    fields: dict[str, Any] = {}
    for index, (raw_name, raw_spec) in enumerate(declared.items()):
        key = str(raw_name)
        spec: dict[str, Any] = raw_spec if isinstance(raw_spec, dict) else {}
        annotation = _annotation(spec, name=f"{name}_{index}")
        field = Field(
            # Optional fields default to None and are dropped when the content is dumped,
            # so an omitted field is absent rather than present-and-null. A declared field
            # holding null would satisfy "is it there?" and fail every reader after that.
            ... if key in needed else None,
            description=_guidance(spec),
            # A contract property need not be a Python identifier, and a model whose field
            # names are not the contract's field names is a model whose output the
            # contract check would reject wholesale.
            alias=key,
        )
        if key not in needed:
            annotation = annotation | None
        fields[_attribute_name(key, taken=fields)] = (annotation, field)

    return create_model(
        name,
        __config__=ConfigDict(extra="forbid", populate_by_name=True),
        **fields,
    )


def _annotation(spec: dict[str, Any], *, name: str) -> Any:
    kind = str(spec.get("type", ""))
    if kind == "object":
        return _object_model(spec, name=f"{name}_object")
    if kind == "array":
        items = spec.get("items")
        inner = _annotation(items if isinstance(items, dict) else {}, name=f"{name}_item")
        return list[inner]  # type: ignore[valid-type]
    return _SCALARS.get(kind, _UNKNOWN)


def _guidance(spec: dict[str, Any]) -> str | None:
    """What the contract says this field is for, as the schema's description.

    Title and description both, because a contract often carries only one of them and the
    model is reading this to decide what to write.
    """
    parts = [str(spec[key]) for key in ("title", "description") if isinstance(spec.get(key), str)]
    return " — ".join(parts) or None


def _attribute_name(key: str, *, taken: dict[str, Any]) -> str:
    """A Python attribute for a contract field name, distinct from the ones already used.

    The alias carries the real name to and from the wire, so this only has to be a legal,
    unique identifier. Collisions are possible in principle — ``a.b`` and ``a-b`` sanitise
    alike — and a silently merged pair of fields would lose one of them.
    """
    candidate = _NON_IDENTIFIER.sub("_", key)
    if not candidate or keyword.iskeyword(candidate) or candidate.startswith(("_", "model_")):
        candidate = f"field_{candidate.lstrip('_')}"
    while candidate in taken:
        candidate = f"{candidate}_"
    return candidate


def _class_name(name: str) -> str:
    """A CamelCase-ish class name from a section key. Cosmetic; it reaches logs and titles."""
    cleaned = _NON_IDENTIFIER.sub("_", name)
    return "".join(part[:1].upper() + part[1:] for part in cleaned.split("_") if part) or "Section"
