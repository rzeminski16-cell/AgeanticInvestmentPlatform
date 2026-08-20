"""Rendering a section from its contract alone, with no template.

**This module is what makes a user-authored section possible.** If rendering needed a
template per section, then adding one would mean writing one — and a section authored in a
natural-language skill file has nobody to write it. So the renderer walks the section's
``output_contract`` (a JSON Schema) and produces a sequence of **fragments** from the
shape it describes; a serialiser turns fragments into Markdown here, and into HTML in
:mod:`aer.render.html`. One walk, several notations — which is what stops the preview, the
PDF and the Markdown export drifting into three accounts of the same content (task 46).

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

**Prose blocks are one more.** An array of objects carrying ``text`` and, optionally, a
``lead_in`` renders as paragraphs — the lead-in emphasised as the opening run — rather
than as a two-column table. It is the structured home for what a model reaches for when
it bolds a sentence opener (gap R6), and like citation, it is a key-name convention
rather than a section's.

**A period series renders as a financial table** (gap R9). An array of objects carrying
``label`` and ``values`` — each value an object with a ``period`` — becomes a table with
periods across the top and line items down the side, so a trend is visible without
arithmetic. Each cell cites its own stored figure; a period a row does not carry renders
as an em dash, never as a silent blank.

**Markdown in prose is stripped, not obeyed.** Model text intermittently arrives with
``**bold**`` markers a live report printed as literal asterisks. Instructions to the
model are advisory; the renderer is not — paired emphasis markers are removed from every
prose string at render, and the stored content keeps exactly what the model produced.

Built-in sections may register an override template later. The generic path is the default,
and it is the one the tests exercise — an override that was never compared against the
generic output would be an override nobody could tell was necessary.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Final

from aer.config import HouseStyle
from aer.render import display

__all__ = [
    "Banner",
    "Bullet",
    "Bullets",
    "CitationRef",
    "Fragment",
    "Heading",
    "Paragraph",
    "RenderedSection",
    "StatusLine",
    "Table",
    "TableRow",
    "markdown_lines",
    "render_section",
]

# The keys that make an object a cited item. Names rather than positions, so a section
# author gets a citation by naming a field.
CITATION_KEYS = ("source_document_id", "calculation_id")

# Keys that carry provenance rather than content: never rendered as text, only turned into
# footnotes. ``financial_fact_id`` and ``extraction_id`` are provenance the numeral rule
# reads and no reader can follow — they name rows in this platform's own tables — so they
# are hidden here exactly as the numeral rule exempts them.
_METADATA_KEYS = frozenset({*CITATION_KEYS, "confidence", "financial_fact_id", "extraction_id"})

_HEADING_BASE = 2


@dataclass(frozen=True, slots=True)
class CitationRef:
    """One thing a rendered fragment cites."""

    kind: str
    identifier: str
    label: str = ""

    def __str__(self) -> str:
        return f"{self.kind}:{self.identifier}"


# -- Fragments ------------------------------------------------------------------------------
#
# The format-neutral middle: what the walk produces and every serialiser consumes. Footnote
# markers are *numbers* here — global across the document, already assigned — so no
# serialiser can renumber, and the Markdown and HTML of one document cannot disagree about
# which marker sits where.


@dataclass(frozen=True, slots=True)
class Heading:
    level: int
    text: str


@dataclass(frozen=True, slots=True)
class Banner:
    """A degradation warning, shown before the content it qualifies."""

    text: str


@dataclass(frozen=True, slots=True)
class StatusLine:
    """Why there is no content — "did not apply", "could not be generated"."""

    text: str


@dataclass(frozen=True, slots=True)
class Paragraph:
    """Prose, or — when ``pairs`` is set — a described object's label-value runs.

    Pairs rather than pre-formatted text, because emphasis is notation: Markdown wants
    ``**Label:** value`` and HTML wants ``<strong>``, and a fragment carrying either
    would leak one serialiser's syntax into the other's output.
    """

    text: str = ""
    markers: tuple[int, ...] = ()
    pairs: tuple[tuple[str, str], ...] | None = None


@dataclass(frozen=True, slots=True)
class Bullet:
    text: str = ""
    markers: tuple[int, ...] = ()
    pairs: tuple[tuple[str, str], ...] | None = None


@dataclass(frozen=True, slots=True)
class Bullets:
    items: tuple[Bullet, ...]


@dataclass(frozen=True, slots=True)
class TableRow:
    """``markers`` land on the last cell; ``cell_markers``, when set, align one marker
    tuple per cell — the period-series shape, where every cell is its own cited figure."""

    cells: tuple[str, ...]
    markers: tuple[int, ...] = ()
    cell_markers: tuple[tuple[int, ...], ...] = ()


@dataclass(frozen=True, slots=True)
class Table:
    """Column display names come from the contract, already humanised by the walk."""

    columns: tuple[str, ...]
    rows: tuple[TableRow, ...]


Fragment = Heading | Banner | StatusLine | Paragraph | Bullets | Table


@dataclass(slots=True)
class RenderedSection:
    """A section as fragments and as Markdown, plus what it cited, in marker order."""

    title: str
    key: str
    markdown: str
    citations: list[CitationRef] = field(default_factory=list)
    fragments: tuple[Fragment, ...] = ()


def render_section(
    *,
    key: str,
    title: str,
    contract: dict[str, Any],
    content: dict[str, Any] | None,
    heading_level: int = _HEADING_BASE,
    footnote_start: int = 1,
    status_note: str | None = None,
    warning: str | None = None,
    style: HouseStyle | None = None,
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
        warning: A degradation banner — insufficient evidence, truncated evidence, a
            recorded failure reason — rendered above the content so a reader meets the
            limitation before the analysis, never as a footnote after it.
        style: The house style every published value is formatted in (gap R1, ADR 0056).
            Defaults so a caller without settings still formats deterministically; the
            stored content is untouched either way — formatting is a projection applied
            at render, never a rewrite.

    Returns:
        The fragments, their Markdown, and the citations in marker order, so the caller
        can build the footnote block.
    """
    fragments: list[Fragment] = [Heading(level=heading_level, text=title)]
    citations: list[CitationRef] = []

    if warning:
        fragments.append(Banner(text=warning))

    if not content:
        fragments.append(
            StatusLine(text=status_note or "No content was produced for this section.")
        )
        return _rendered(title=title, key=key, fragments=fragments, citations=citations)

    # Model prose intermittently arrives double-escaped: a literal backslash-u-2014 in
    # the stored text where an em dash belongs, which a live report printed verbatim
    # mid-sentence. Normalised at render, once, for every notation — and markdown
    # emphasis is stripped on the same pass (gap R6): the stored content is untouched,
    # and no notation shows a reader literal asterisks.
    content = _unbolded(_unescaped(content))
    active = style if style is not None else HouseStyle()

    for name, subschema in _ordered_properties(contract):
        if name in _METADATA_KEYS or name not in content:
            continue
        value = content[name]
        if value in (None, "", [], {}):
            continue

        field_title = str(subschema.get("title") or _humanise(name))
        fragments.extend(
            _value_fragments(
                value,
                citations=citations,
                footnote_start=footnote_start,
                heading_level=heading_level + 1,
                field_title=field_title,
                subschema=subschema,
                style=active,
            )
        )

    return _rendered(title=title, key=key, fragments=fragments, citations=citations)


_LITERAL_ESCAPE: Final[re.Pattern[str]] = re.compile(r"\\u([0-9a-fA-F]{4})")


def _unescaped(value: Any) -> Any:
    """The value with literal ``\\uXXXX`` sequences decoded to their characters.

    Applied to prose only at render time; the stored content is untouched, so the
    artefact trail still shows exactly what the model produced.
    """
    if isinstance(value, str):
        return _LITERAL_ESCAPE.sub(lambda match: chr(int(match.group(1), 16)), value)
    if isinstance(value, dict):
        return {key: _unescaped(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_unescaped(item) for item in value]
    return value


# Paired emphasis only. A lone ``**`` is left exactly as written: stripping it would turn
# a literal the author meant into silence, and an unpaired marker never renders as bold
# anywhere either.
_INLINE_BOLD: Final[re.Pattern[str]] = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)


def _unbolded(value: Any) -> Any:
    """The value with paired ``**`` markers removed from every string (gap R6).

    The text between the markers survives; only the notation goes. Emphasis a section
    wants is expressed through structure — a ``lead_in`` prose block — which every
    serialiser renders in its own notation instead of one notation leaking into another.
    """
    if isinstance(value, str):
        return _INLINE_BOLD.sub(r"\1", value)
    if isinstance(value, dict):
        return {key: _unbolded(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_unbolded(item) for item in value]
    return value


def _rendered(
    *, title: str, key: str, fragments: list[Fragment], citations: list[CitationRef]
) -> RenderedSection:
    return RenderedSection(
        title=title,
        key=key,
        markdown="\n".join(markdown_lines(fragments)),
        citations=citations,
        fragments=tuple(fragments),
    )


# -- The Markdown notation -------------------------------------------------------------------


def markdown_lines(fragments: list[Fragment] | tuple[Fragment, ...]) -> list[str]:
    """Fragments as Markdown lines, each block carrying its own trailing blank.

    This is the notation the golden-document test holds byte for byte: the walk moved to
    fragments in task 46 and this function is where the exact pre-refactor line shapes
    live on.
    """
    lines: list[str] = []
    for fragment in fragments:
        match fragment:
            case Heading(level=level, text=text):
                lines.extend([f"{'#' * level} {text}", ""])
            case Banner(text=text):
                lines.extend([f"> **{text}**", ""])
            case StatusLine(text=text):
                lines.extend([f"*{text}*", ""])
            case Paragraph(markers=markers) as paragraph:
                lines.extend([f"{_prose(paragraph)}{_marks(markers)}", ""])
            case Bullets(items=items):
                lines.extend(f"- {_prose(item)}{_marks(item.markers)}" for item in items)
                lines.append("")
            case Table(columns=columns, rows=rows):
                lines.append("| " + " | ".join(columns) + " |")
                lines.append("|" + "|".join("---" for _ in columns) + "|")
                for row in rows:
                    cells = list(row.cells)
                    if row.cell_markers:
                        # A period series: every cell is its own cited figure.
                        cells = [
                            f"{cell}{_marks(marks)}"
                            for cell, marks in zip(cells, row.cell_markers, strict=True)
                        ]
                    # The marker goes on the last cell, which is where a reader looks for
                    # the provenance of a row.
                    if row.markers:
                        cells[-1] = f"{cells[-1]}{_marks(row.markers)}"
                    lines.append("| " + " | ".join(cells) + " |")
                lines.append("")
    return lines


def _marks(markers: tuple[int, ...]) -> str:
    return "".join(f"[^{number}]" for number in markers)


def _prose(fragment: Paragraph | Bullet) -> str:
    """A paragraph's or bullet's text in Markdown: pair runs bolded, plain text as is."""
    if fragment.pairs is None:
        return fragment.text
    return " — ".join(f"**{label}:** {value}" for label, value in fragment.pairs)


# -- The walk --------------------------------------------------------------------------------


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


def _value_fragments(
    value: Any,
    *,
    citations: list[CitationRef],
    footnote_start: int,
    heading_level: int,
    field_title: str,
    subschema: dict[str, Any] | None = None,
    style: HouseStyle,
) -> list[Fragment]:
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
        return _list_fragments(
            value,
            citations=citations,
            footnote_start=footnote_start,
            heading_level=heading_level,
            field_title=field_title,
            subschema=subschema,
            style=style,
        )

    heading = Heading(level=heading_level, text=field_title)
    if isinstance(value, dict):
        markers = _cite(value, citations=citations, footnote_start=footnote_start)
        return [heading, Paragraph(markers=markers, pairs=_pairs(value, style=style))]

    return [heading, Paragraph(text=display.scalar(value, style=style))]


def _list_fragments(
    values: list[Any],
    *,
    citations: list[CitationRef],
    footnote_start: int,
    heading_level: int,
    field_title: str,
    subschema: dict[str, Any] | None = None,
    style: HouseStyle,
) -> list[Fragment]:
    heading = Heading(level=heading_level, text=field_title)

    if all(not isinstance(item, dict) for item in values):
        return [
            heading,
            Bullets(items=tuple(Bullet(text=display.scalar(item, style=style)) for item in values)),
        ]

    if _prose_blocks(values):
        fragments: list[Fragment] = [heading]
        for item in values:
            markers = _cite(item, citations=citations, footnote_start=footnote_start)
            lead = str(item.get("lead_in") or "").strip()
            text = str(item.get("text") or "")
            if lead:
                fragments.append(Paragraph(markers=markers, pairs=((lead, text),)))
            else:
                fragments.append(Paragraph(text=text, markers=markers))
        return fragments

    if _period_series(values):
        return [
            heading,
            _series_table(values, citations=citations, footnote_start=footnote_start, style=style),
        ]

    columns = _shared_columns(values, subschema=subschema)
    if columns:
        rows = []
        for item in values:
            markers = _cite(item, citations=citations, footnote_start=footnote_start)
            rows.append(
                TableRow(
                    cells=tuple(display.cell(item, column, style=style) for column in columns),
                    markers=markers,
                )
            )
        return [
            heading,
            Table(columns=tuple(_humanise(c) for c in columns), rows=tuple(rows)),
        ]

    items: list[Bullet] = []
    for item in values:
        if isinstance(item, dict):
            markers = _cite(item, citations=citations, footnote_start=footnote_start)
            items.append(Bullet(markers=markers, pairs=_pairs(item, style=style)))
        else:
            items.append(Bullet(text=display.scalar(item, style=style)))
    return [heading, Bullets(items=tuple(items))]


def _prose_blocks(values: list[Any]) -> bool:
    """Whether a list is prose blocks: objects of ``text`` with an optional ``lead_in``.

    Judged over the whole list, because the alternative rendering is a table and a table
    must hold every row. Content keys only — a block still cites through the metadata
    keys, exactly as a table row does.
    """
    if not values:
        return False
    for item in values:
        if not isinstance(item, dict) or "text" not in item:
            return False
        if {key for key in item if key not in _METADATA_KEYS} - {"lead_in", "text"}:
            return False
    return True


def _period_series(values: list[Any]) -> bool:
    """Whether a list is a period series: rows of ``label`` and period-keyed ``values``.

    Every row must fit — the rendering is one table and a table must hold every row —
    and every entry must name its period, because a value without a period has no column
    to sit in.
    """
    if not values:
        return False
    for item in values:
        if not isinstance(item, dict) or "values" not in item:
            return False
        if {key for key in item if key not in _METADATA_KEYS} - {"label", "values"}:
            return False
        entries = item["values"]
        if not isinstance(entries, list) or not entries:
            return False
        if any(not isinstance(entry, dict) or "period" not in entry for entry in entries):
            return False
    return True


def _series_table(
    values: list[Any],
    *,
    citations: list[CitationRef],
    footnote_start: int,
    style: HouseStyle,
) -> Table:
    """A period series as a financial table: periods across, line items down (gap R9).

    Column order is first appearance across the rows — the author writes oldest first
    and the table reads left to right. Every cell registers its own citations, and a
    period a row does not carry is an em dash rather than a blank that reads as zero.
    """
    periods: list[str] = []
    for item in values:
        for entry in item["values"]:
            period = str(entry["period"])
            if period not in periods:
                periods.append(period)

    rows: list[TableRow] = []
    for item in values:
        label = str(item.get("label") or "")
        by_period: dict[str, dict[str, Any]] = {}
        for entry in item["values"]:
            by_period.setdefault(str(entry["period"]), entry)

        cells: list[str] = [label]
        cell_markers: list[tuple[int, ...]] = [()]
        for period in periods:
            entry = by_period.get(period)
            value = None if entry is None else entry.get("value")
            if entry is None or value is None or (isinstance(value, str) and not value.strip()):
                # A period the row does not carry, and a row whose value never resolved,
                # read the same: an em dash with no footnote — a marker on an absent
                # figure rendered as a bare number in the MTB report (gap A66).
                cells.append("\N{EM DASH}")
                cell_markers.append(())
                continue
            # The row's label rides into the cell formatting: "Operating margin" is what
            # tells the formatter a bare ratio reads as a percentage (ADR 0056).
            cells.append(display.cell({"label": label, **entry}, "value", style=style))
            cell_markers.append(_cite(entry, citations=citations, footnote_start=footnote_start))
        rows.append(TableRow(cells=tuple(cells), cell_markers=tuple(cell_markers)))

    return Table(columns=("", *periods), rows=tuple(rows))


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
            return _without_consumed_unit(ordered)

    # No usable contract: fall back to the first row's order, which is at least stable
    # across the rows because they were just checked to share a key set.
    return _without_consumed_unit([key for key in dicts[0] if key not in _METADATA_KEYS])


def _without_consumed_unit(columns: list[str]) -> list[str]:
    """Drop the ``unit`` column when a ``value`` column will carry the unit itself.

    The formatter renders "$109,417m" in the value cell, and a "Unit" column beside it
    saying "USD" is the machine's bookkeeping shown to a reader (gap R1).
    """
    if "value" in columns and "unit" in columns:
        return [name for name in columns if name != "unit"]
    return columns


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


def _cite(
    item: dict[str, Any], *, citations: list[CitationRef], footnote_start: int
) -> tuple[int, ...]:
    """Register any citations on an object and return its footnote numbers.

    An object citing both a calculation and a source document gets two markers — a
    calculated figure rests on both the arithmetic and the evidence beneath it, and a
    reader chasing one should not have to guess that the other exists.
    """
    markers: list[int] = []
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
        markers.append(number)

    return tuple(markers)


def _pairs(item: dict[str, Any], *, style: HouseStyle) -> tuple[tuple[str, str], ...]:
    """An object with no shared shape, as label-value runs for a one-line rendering.

    The ``unit`` run disappears when a ``value`` sits beside it: the formatted value
    carries its unit — "$109,417m" — and "Unit: USD" after it is the machine talking.
    """
    hidden = _METADATA_KEYS | ({"unit"} if "value" in item and "unit" in item else set())
    return tuple(
        (_humanise(key), display.cell(item, key, style=style))
        for key, value in item.items()
        if key not in hidden and value not in (None, "", [], {})
    )


def _humanise(name: str) -> str:
    """``period_end`` becomes ``Period End``. Used when a contract omits a title."""
    return name.replace("_", " ").strip().title()
