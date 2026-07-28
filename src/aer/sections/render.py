"""Rendering a section from its contract alone, with no template.

**This module is what makes a user-authored section possible.** If rendering needed a
template per section, then adding one would mean writing one — and a section authored in a
natural-language skill file has nobody to write it. So the renderer walks the section's
``output_contract`` (a JSON Schema) and produces Markdown from the shape it describes.

The whole convention is four rules:

* An **object** renders its properties in the contract's declared order, each under its
  ``title`` as a sub-heading.
* An **array of strings** renders as a bullet list.
* An **array of objects** renders as a table if the objects share a shape, and as
  sub-sections if they do not.
* A **string** renders as a paragraph.

**Citation is one further rule.** Any object carrying ``source_document_id`` or
``calculation_id`` is a *cited item*: it renders normally and gets a footnote marker. That
is the only coupling between content and provenance, and it is a key name rather than a
position, so a section author gets citations by naming a field rather than by knowing where
the renderer looks.

Built-in sections may register an override template later. The generic path is the default,
and it is the one the tests exercise — an override that was never compared against the
generic output would be an override nobody could tell was necessary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

__all__ = ["CitationRef", "RenderedSection", "render_section"]

# The keys that make an object a cited item. Names rather than positions, so a section
# author gets a citation by naming a field.
CITATION_KEYS = ("source_document_id", "calculation_id")

# Keys that carry provenance rather than content: never rendered as text, only turned into
# footnotes.
_METADATA_KEYS = frozenset({*CITATION_KEYS, "confidence"})

_HEADING_BASE = 2


@dataclass(frozen=True, slots=True)
class CitationRef:
    """One thing a rendered fragment cites."""

    kind: str
    identifier: str
    label: str = ""

    def __str__(self) -> str:
        return f"{self.kind}:{self.identifier}"


@dataclass(slots=True)
class RenderedSection:
    """A section as Markdown, plus what it cited, in the order the markers appear."""

    title: str
    key: str
    markdown: str
    citations: list[CitationRef] = field(default_factory=list)


def render_section(
    *,
    key: str,
    title: str,
    contract: dict[str, Any],
    content: dict[str, Any] | None,
    heading_level: int = _HEADING_BASE,
    footnote_start: int = 1,
    status_note: str | None = None,
) -> RenderedSection:
    """Render one section's content against its contract.

    Args:
        contract: The section's ``output_contract``. Supplies field order and the headings.
        content: What the section produced, or ``None`` if it produced nothing.
        footnote_start: The next unused footnote number. Numbering is global across the
            report, so it is passed in rather than restarting per section.
        status_note: Shown in place of content when there is none — "this section did not
            apply", "generation failed". An absence with no explanation reads as an
            oversight.

    Returns:
        The Markdown and the citations in marker order, so the caller can build the
        footnote block.
    """
    lines = [f"{'#' * heading_level} {title}", ""]
    citations: list[CitationRef] = []

    if not content:
        lines.append(f"*{status_note or 'No content was produced for this section.'}*")
        lines.append("")
        return RenderedSection(title=title, key=key, markdown="\n".join(lines), citations=citations)

    properties = _ordered_properties(contract)
    for name, subschema in properties:
        if name in _METADATA_KEYS or name not in content:
            continue
        value = content[name]
        if value in (None, "", [], {}):
            continue

        field_title = str(subschema.get("title") or _humanise(name))
        rendered = _render_value(
            value,
            citations=citations,
            footnote_start=footnote_start,
            heading_level=heading_level + 1,
            field_title=field_title,
            subschema=subschema,
        )
        lines.extend(rendered)

    return RenderedSection(title=title, key=key, markdown="\n".join(lines), citations=citations)


def _ordered_properties(contract: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """The contract's properties, in declaration order.

    Python preserves insertion order, and ``section_definitions.output_contract`` is stored
    as ``json`` rather than ``jsonb`` precisely so that order survives the database — jsonb
    reorders keys by length and then bytewise, silently. So the author's declared order is
    the rendered order, which is what lets a section author control the shape of their
    output without a template.
    """
    properties = contract.get("properties")
    if not isinstance(properties, dict):
        return []
    return [(name, sub if isinstance(sub, dict) else {}) for name, sub in properties.items()]


def _render_value(
    value: Any,
    *,
    citations: list[CitationRef],
    footnote_start: int,
    heading_level: int,
    field_title: str,
    subschema: dict[str, Any] | None = None,
) -> list[str]:
    """Render one field.

    Dispatches on the *value*, not the declared type. The contract supplies the ordering
    and the headings; the value decides the shape. A section whose content disagrees with
    its own schema is a validation problem, and rendering is not the place to discover it
    -- by here the content has already been validated, and rendering what is actually
    present beats rendering what was promised.

    ``subschema`` is the field's own schema, carried down so a table's columns can come
    from the contract rather than from whatever key order the content happens to have.
    """
    if isinstance(value, list):
        return _render_list(
            value,
            citations=citations,
            footnote_start=footnote_start,
            heading_level=heading_level,
            field_title=field_title,
            subschema=subschema,
        )

    if isinstance(value, dict):
        marker = _cite(value, citations=citations, footnote_start=footnote_start)
        body = _describe(value)
        return [f"{'#' * heading_level} {field_title}", "", f"{body}{marker}", ""]

    return [f"{'#' * heading_level} {field_title}", "", f"{value}", ""]


def _render_list(
    values: list[Any],
    *,
    citations: list[CitationRef],
    footnote_start: int,
    heading_level: int,
    field_title: str,
    subschema: dict[str, Any] | None = None,
) -> list[str]:
    lines = [f"{'#' * heading_level} {field_title}", ""]

    if all(not isinstance(item, dict) for item in values):
        lines.extend(f"- {item}" for item in values)
        lines.append("")
        return lines

    columns = _shared_columns(values, subschema=subschema)
    if columns:
        lines.extend(
            _render_table(
                values,
                columns=columns,
                citations=citations,
                footnote_start=footnote_start,
            )
        )
        lines.append("")
        return lines

    for item in values:
        if isinstance(item, dict):
            marker = _cite(item, citations=citations, footnote_start=footnote_start)
            lines.append(f"- {_describe(item)}{marker}")
        else:
            lines.append(f"- {item}")
    lines.append("")
    return lines


def _shared_columns(values: list[Any], *, subschema: dict[str, Any] | None = None) -> list[str]:
    """The content columns every object in a list has, or nothing if they differ.

    A table is only honest when the rows share a shape. Objects with different keys
    rendered as a table would produce empty cells that read as missing data rather than as
    a section whose items are simply not tabular.

    **Order comes from the contract when the contract describes the item.** The values
    decide *whether* a table is appropriate; the author decides what order its columns go
    in. Taking the order from the first row would hand that decision to whatever built the
    content — and a figure written as ``{"value": ..., "label": ...}`` would silently
    transpose a table its author had laid out deliberately.
    """
    dicts = [item for item in values if isinstance(item, dict)]
    if len(dicts) != len(values) or not dicts:
        return []

    present = {key for key in dicts[0] if key not in _METADATA_KEYS}
    if not present:
        return []
    for item in dicts[1:]:
        if {key for key in item if key not in _METADATA_KEYS} != present:
            return []

    declared = _declared_item_properties(subschema)
    if declared:
        # The contract's order, restricted to what the rows actually carry. A declared
        # column no row has would render as a column of blanks, which reads as missing
        # data rather than as a field this content does not use.
        ordered = [name for name in declared if name in present]
        if set(ordered) == present:
            return ordered

    # No usable contract: fall back to the first row's order, which is at least stable
    # across the rows because they were just checked to share a key set.
    return [key for key in dicts[0] if key not in _METADATA_KEYS]


def _declared_item_properties(subschema: dict[str, Any] | None) -> list[str]:
    """The property names an array field declares for its items, in declared order."""
    if not isinstance(subschema, dict):
        return []
    items = subschema.get("items")
    if not isinstance(items, dict):
        return []
    properties = items.get("properties")
    if not isinstance(properties, dict):
        return []
    return [name for name in properties if name not in _METADATA_KEYS]


def _render_table(
    values: list[Any], *, columns: list[str], citations: list[CitationRef], footnote_start: int
) -> list[str]:
    header = "| " + " | ".join(_humanise(c) for c in columns) + " |"
    divider = "|" + "|".join("---" for _ in columns) + "|"
    lines = [header, divider]

    for item in values:
        marker = _cite(item, citations=citations, footnote_start=footnote_start)
        cells = [str(item.get(column, "")) for column in columns]
        # The marker goes on the last cell, which is where a reader looks for the
        # provenance of a row.
        if marker:
            cells[-1] = f"{cells[-1]}{marker}"
        lines.append("| " + " | ".join(cells) + " |")

    return lines


def _cite(item: dict[str, Any], *, citations: list[CitationRef], footnote_start: int) -> str:
    """Register any citations on an object and return the footnote marker.

    An object citing both a calculation and a source document gets two markers — a
    calculated figure rests on both the arithmetic and the evidence beneath it, and a
    reader chasing one should not have to guess that the other exists.
    """
    markers: list[str] = []
    for key in CITATION_KEYS:
        identifier = item.get(key)
        if not identifier:
            continue
        kind = "calculation" if key == "calculation_id" else "source_document"
        reference = CitationRef(
            kind=kind,
            identifier=str(identifier),
            label=str(item.get("label", "")),
        )

        if reference in citations:
            number = footnote_start + citations.index(reference)
        else:
            citations.append(reference)
            number = footnote_start + len(citations) - 1
        markers.append(f"[^{number}]")

    return "".join(markers)


def _describe(item: dict[str, Any]) -> str:
    """A one-line rendering of an object with no shared shape."""
    parts = [
        f"**{_humanise(key)}:** {value}"
        for key, value in item.items()
        if key not in _METADATA_KEYS and value not in (None, "", [], {})
    ]
    return " — ".join(parts)


def _humanise(name: str) -> str:
    """``period_end`` becomes ``Period End``. Used when a contract omits a title."""
    return name.replace("_", " ").strip().title()
