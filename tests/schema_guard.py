"""Whether the API would accept a schema, asked with the vendor's own transformation.

Gap A18. `aer.providers.fake` cannot answer this question: it may not import the SDK (see
:mod:`aer.providers`, and the test that scans the source tree for it), and re-deriving the
dialect by hand would drift the first time it moved. So the check lives here, on the test
side where the SDK is fair game, and is injected into the fake through its
``inspect_schema`` hook.

**What it looks for is the shape that emptied every section of the first real report.** A
field declared as ``dict[str, Any]`` becomes, after the SDK's transformation::

    {"type": "object", "properties": {}, "additionalProperties": false}

— an object with no permitted keys. The API enforced it exactly, the model returned ``{}``,
and eighteen sections rendered as "could not be generated". Every test passed throughout,
because a fake answers from a script and never sees the schema at all.

Wiring this into the fake means every workflow test now asks the question the live run
asked, on the schema each call actually composed — which is a stronger guarantee than
`tests/test_contract_schema.py` alone, since that one checks the *registered* contracts and
this one checks whatever a call narrowed them to.
"""

from __future__ import annotations

from typing import Any

from anthropic import transform_schema
from pydantic import BaseModel

__all__ = ["SchemaTheApiWouldRejectError", "inexpressible_fields", "refuse_unanswerable_schema"]


class SchemaTheApiWouldRejectError(AssertionError):
    """A contract the model would be unable to answer. An assertion, because it is a bug."""


def inexpressible_fields(node: Any, path: str = "") -> list[str]:
    """Paths in a transformed schema that no reply could satisfy.

    Walks the schema *after* the SDK's transformation, because that is where the closure
    is applied and it is the exact shape that silently empties a section.
    """
    found: list[str] = []
    if isinstance(node, dict):
        closed = node.get("additionalProperties") is False
        if node.get("type") == "object" and closed and not node.get("properties"):
            found.append(path)
        for key, value in node.items():
            if isinstance(value, dict):
                found.extend(inexpressible_fields(value, f"{path}.{key}"))
            elif isinstance(value, list):
                for index, item in enumerate(value):
                    found.extend(inexpressible_fields(item, f"{path}.{key}[{index}]"))
    return found


def refuse_unanswerable_schema(schema: type[BaseModel]) -> None:
    """Raise if the API would be handed a contract permitting an empty answer.

    Raises:
        SchemaTheApiWouldRejectError: Naming the field. An `AssertionError` rather than an
            application error, because a caller composing one is a defect in this
            repository and not a condition to handle.
    """
    empty = inexpressible_fields(transform_schema(schema), schema.__name__)
    if not empty:
        return

    message = (
        f"{schema.__name__} would reach the API with {', '.join(empty)} unable to hold "
        "anything: an object with no permitted keys. The model can only answer {} there, "
        "which is how every section of the first real report came back empty. Narrow the "
        "field at call time with `response_schema`."
    )
    raise SchemaTheApiWouldRejectError(message)
