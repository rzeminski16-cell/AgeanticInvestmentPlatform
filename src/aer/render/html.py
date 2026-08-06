"""The HTML notation of an assembled report — the one the preview and the PDF share.

This is the "what is approved is what exists" surface: the Gate 2 preview renders exactly
this HTML, and task 48's WeasyPrint pass consumes exactly this HTML, so approving the
preview is approving the PDF's input byte for byte. The stylesheet is inline and written
for CSS paged media (cover page, running headers, page numbers, bookmarks come from the
heading structure), which a browser mostly ignores and a print engine honours — one
document, two readers.

**Model output is data, never markup.** Every piece of content passes through
:func:`markupsafe.escape` on its way into a tag, and the Jinja environment autoescapes
everything else. A section whose content contains ``<script>`` renders as text saying so.

**Footnote markers arrive numbered.** The fragments carry the numbers the assembler
assigned, so this notation cannot disagree with the Markdown one about which marker sits
where — a property the tests hold rather than assume.
"""

from __future__ import annotations

from jinja2 import Environment, PackageLoader, select_autoescape
from markupsafe import Markup, escape

from aer.charts import svg_data_uri
from aer.render.document import (
    CalculationFootnote,
    ChartView,
    Footnote,
    ReportDocument,
    SourceFootnote,
    UnresolvedFootnote,
)
from aer.sections.render import (
    Banner,
    Bullet,
    Bullets,
    Fragment,
    Heading,
    Paragraph,
    StatusLine,
    Table,
)

__all__ = ["render_html"]

_ENV = Environment(
    loader=PackageLoader("aer.render", "templates"),
    autoescape=select_autoescape(default=True, default_for_string=True),
    trim_blocks=True,
    lstrip_blocks=True,
)


def render_html(document: ReportDocument) -> str:
    """One :class:`ReportDocument`, as a self-contained HTML page."""
    seen: set[int] = set()
    sections = [
        {
            "key": section.key,
            "title": section.title,
            "origin": section.origin,
            "body": _blocks(section.fragments, seen=seen),
        }
        for section in document.sections
    ]
    custom = [section for section in sections if section["origin"] == "skill"]
    return _ENV.get_template("report.html").render(
        document=document,
        header=document.header,
        sections=sections,
        custom_sections=custom,
        builtin_sections=[s for s in sections if s["origin"] != "skill"],
        charts=[_chart(chart, seen=seen) for chart in document.charts],
        footnotes=[_footnote(footnote) for footnote in document.footnotes],
        referenced=sorted(seen),
        disclaimer_html=_emphasise(document.disclaimer),
        comps_html=(_emphasise(document.comps_paragraph) if document.comps_paragraph else None),
    )


def _emphasise(text: str) -> Markup:
    """Paired ``**`` emphasis as ``<strong>``, everything else escaped.

    The disclaimer and the withheld-comps paragraph are written once, in prose modules
    that predate the HTML notation, with Markdown emphasis in the string. Showing a
    reader literal asterisks would be a notation leak in the other direction, so the one
    Markdown convention those strings use is converted here — and only that one.
    """
    pieces = text.split("**")
    # An odd piece count means every ** was paired; with an even count the final piece
    # follows a dangling marker and stays plain rather than being silently emphasised.
    balanced = len(pieces) % 2 == 1
    rendered: list[Markup] = []
    for index, piece in enumerate(pieces):
        if index % 2 == 1 and (balanced or index < len(pieces) - 1):
            rendered.append(Markup(f"<strong>{escape(piece)}</strong>"))
        else:
            rendered.append(Markup(escape(piece)))
    return Markup("").join(rendered)


# -- Fragments as HTML -----------------------------------------------------------------------


def _blocks(fragments: tuple[Fragment, ...], *, seen: set[int]) -> Markup:
    """Fragments as HTML blocks — the same walk the Markdown notation transcribes.

    ``seen`` tracks which footnote numbers have already had a marker: the first marker
    for a number carries ``id="fnref-{n}"`` so its footnote can link back to it, and a
    reused marker (within-section de-duplication) links forward only.
    """
    parts: list[Markup] = []
    for fragment in fragments:
        match fragment:
            case Heading(level=level, text=text):
                level = min(level, 6)
                parts.append(Markup(f"<h{level}>{escape(text)}</h{level}>"))
            case Banner(text=text):
                parts.append(Markup(f'<div class="banner" role="note">{escape(text)}</div>'))
            case StatusLine(text=text):
                parts.append(Markup(f'<p class="status-note">{escape(text)}</p>'))
            case Paragraph(markers=markers) as paragraph:
                parts.append(Markup(f"<p>{_prose(paragraph)}{_marks(markers, seen=seen)}</p>"))
            case Bullets(items=items):
                bullets = Markup("").join(
                    Markup(f"<li>{_prose(item)}{_marks(item.markers, seen=seen)}</li>")
                    for item in items
                )
                parts.append(Markup(f"<ul>{bullets}</ul>"))
            case Table(columns=columns, rows=rows):
                head = Markup("").join(
                    Markup(f'<th scope="col">{escape(column)}</th>') for column in columns
                )
                body_rows: list[Markup] = []
                for row in rows:
                    cells = [Markup(f"<td>{escape(cell)}</td>") for cell in row.cells]
                    if row.markers:
                        # The marker goes on the last cell, which is where a reader looks
                        # for the provenance of a row.
                        cells[-1] = Markup(
                            f"<td>{escape(row.cells[-1])}{_marks(row.markers, seen=seen)}</td>"
                        )
                    body_rows.append(Markup(f"<tr>{Markup('').join(cells)}</tr>"))
                parts.append(
                    Markup(
                        f"<table><thead><tr>{head}</tr></thead>"
                        f"<tbody>{Markup('').join(body_rows)}</tbody></table>"
                    )
                )
    return Markup("").join(parts)


def _prose(fragment: Paragraph | Bullet) -> Markup:
    if fragment.pairs is None:
        return Markup(escape(fragment.text))
    return Markup(" — ").join(
        Markup(f"<strong>{escape(label)}:</strong> {escape(value)}")
        for label, value in fragment.pairs
    )


def _marks(markers: tuple[int, ...], *, seen: set[int]) -> Markup:
    parts: list[Markup] = []
    for number in markers:
        anchor = f' id="fnref-{number}"' if number not in seen else ""
        seen.add(number)
        parts.append(
            Markup(f'<sup class="fn-ref"{anchor}><a href="#fn-{number}">{number}</a></sup>')
        )
    return Markup("").join(parts)


# -- Exhibits --------------------------------------------------------------------------------


def _chart(chart: ChartView, *, seen: set[int]) -> dict[str, object]:
    """One exhibit as template data: the SVG as a data URI, the caption with its markers.

    A data URI rather than inline SVG — see :func:`aer.charts.svg_data_uri`.
    """
    return {
        "key": chart.key,
        "title": chart.title,
        "uri": svg_data_uri(chart.svg),
        "caption": Markup(f"{escape(chart.caption)}{_marks(chart.markers, seen=seen)}"),
        "placeholder": chart.placeholder,
    }


# -- Footnotes as display rows ---------------------------------------------------------------


def _footnote(footnote: Footnote) -> dict[str, object]:
    """One footnote as template data; the entry text is built here, escaped."""
    match footnote:
        case CalculationFootnote():
            text = Markup(
                f"Calculated: <code>{escape(footnote.formula)}</code> = "
                f"{escape(footnote.value)} {escape(footnote.unit)} "
                f"(<code>{escape(footnote.function_ref)}</code>, code version "
                f"<code>{escape(footnote.code_version_prefix)}</code>)."
            )
        case SourceFootnote():
            pieces = [Markup(escape(footnote.title))]
            if footnote.publisher:
                pieces.append(Markup(escape(footnote.publisher)))
            if footnote.publication_date:
                pieces.append(Markup(f"published {footnote.publication_date.isoformat()}"))
            pieces.append(Markup(f"retrieved {footnote.retrieved.isoformat()}"))
            pieces.append(Markup(f"tier {escape(footnote.tier)}"))
            joined = Markup(", ").join(pieces)
            text = Markup(
                f'{joined}. <a href="{escape(footnote.url)}" class="src">{escape(footnote.url)}</a>'
            )
        case UnresolvedFootnote():
            text = Markup(
                f"<strong>Unresolved citation</strong> — this claim references "
                f"{escape(footnote.kind_label)} <code>{escape(footnote.identifier)}</code>, "
                "which is no longer present. Do not rely on the figure it supports."
            )
    return {"number": footnote.number, "text": text}
