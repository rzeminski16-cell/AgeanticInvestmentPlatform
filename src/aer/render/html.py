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
from aer.config import HouseStyle
from aer.render import display
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


def render_html(document: ReportDocument, *, contents: bool = True) -> str:
    """One :class:`ReportDocument`, as a self-contained HTML page.

    ``contents=False`` drops the contents page — the one-page summary's shape (gap O8),
    where a table of contents for two sections would be furniture without a house.
    """
    seen: set[int] = set()
    titles = {
        footnote.number: _hover(footnote, style=document.style) for footnote in document.footnotes
    }
    # Before the sections, in document order: the glance's markers are the document's
    # first, and the first marker for a number carries the back-reference anchor.
    glance_html = _blocks(document.glance, seen=seen, titles=titles) if document.glance else None
    sections = [
        {
            "key": section.key,
            "title": section.title,
            "origin": section.origin,
            "generated": section.generated,
            # Body before charts, in document order: the first marker for a number
            # carries the back-reference anchor, and reading order decides which.
            "body": _blocks(section.fragments, seen=seen, titles=titles),
            "charts": [_chart(chart, seen=seen, titles=titles) for chart in section.charts],
        }
        for section in document.sections
    ]
    custom = [section for section in sections if section["origin"] == "skill"]
    return _ENV.get_template("report.html").render(
        document=document,
        glance_html=glance_html,
        contents=contents,
        header=document.header,
        sections=sections,
        custom_sections=custom,
        builtin_sections=[s for s in sections if s["origin"] != "skill"],
        charts=[_chart(chart, seen=seen, titles=titles) for chart in document.charts],
        footnotes=[
            _footnote(footnote, job_id=document.job_id, style=document.style)
            for footnote in document.footnotes
        ],
        as_of_text=display.date_text(document.header.as_of, style=document.style),
        appendix_rows=[
            {
                "title": row.title,
                "url": row.url,
                "publisher": row.publisher,
                "published_text": (
                    display.date_text(row.publication_date, style=document.style)
                    if row.publication_date
                    else "—"
                ),
                "retrieved_text": display.date_text(row.retrieved, style=document.style),
                "tier": row.tier,
                "digest_prefix": row.digest_prefix,
            }
            for row in document.appendix
        ],
        limitations=document.limitations,
        referenced=sorted(seen),
        disclaimer_html=_emphasise(document.disclaimer),
        comps_html=(
            _emphasise(display.prose(document.comps_paragraph, style=document.style))
            if document.comps_paragraph
            else None
        ),
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


def _blocks(fragments: tuple[Fragment, ...], *, seen: set[int], titles: dict[int, str]) -> Markup:
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
                parts.append(
                    Markup(
                        f"<p>{_prose(paragraph)}{_joint(_tail(paragraph), markers)}"
                        f"{_marks(markers, seen=seen, titles=titles)}</p>"
                    )
                )
            case Bullets(items=items):
                bullets = Markup("").join(
                    Markup(
                        f"<li>{_prose(item)}{_joint(_tail(item), item.markers)}"
                        f"{_marks(item.markers, seen=seen, titles=titles)}</li>"
                    )
                    for item in items
                )
                parts.append(Markup(f"<ul>{bullets}</ul>"))
            case Table(columns=columns, rows=rows):
                head = Markup("").join(
                    Markup(f'<th scope="col">{escape(column)}</th>') for column in columns
                )
                body_rows: list[Markup] = []
                for row in rows:
                    if row.cell_markers:
                        # A period series: every cell is its own cited figure.
                        cells = [
                            Markup(
                                f"<td>{_coded(cell)}{_joint(cell, marks)}"
                                f"{_marks(marks, seen=seen, titles=titles)}</td>"
                            )
                            for cell, marks in zip(row.cells, row.cell_markers, strict=True)
                        ]
                    else:
                        cells = [Markup(f"<td>{_coded(cell)}</td>") for cell in row.cells]
                    if row.markers:
                        # The marker goes on the last cell, which is where a reader looks
                        # for the provenance of a row.
                        cells[-1] = Markup(
                            f"<td>{_coded(row.cells[-1])}{_joint(row.cells[-1], row.markers)}"
                            f"{_marks(row.markers, seen=seen, titles=titles)}</td>"
                        )
                    body_rows.append(Markup(f"<tr>{Markup('').join(cells)}</tr>"))
                parts.append(
                    Markup(
                        f"<table><thead><tr>{head}</tr></thead>"
                        f"<tbody>{Markup('').join(body_rows)}</tbody></table>"
                    )
                )
    return Markup("").join(parts)


def _coded(text: str) -> Markup:
    """A table cell with paired backticks rendered as ``<code>``, everything else escaped.

    The findings table quotes a failed check's own strings as code spans (gap R9) — the
    Markdown notation carries the backticks natively, and a reader of the HTML must not
    see them as literal punctuation. Same pairing discipline as :func:`_emphasise`: a
    dangling backtick stays a backtick rather than silently swallowing the rest of the
    cell.
    """
    if "`" not in text:
        return Markup(escape(text))
    pieces = text.split("`")
    balanced = len(pieces) % 2 == 1
    rendered: list[Markup] = []
    for index, piece in enumerate(pieces):
        if index % 2 == 1 and (balanced or index < len(pieces) - 1):
            rendered.append(Markup(f"<code>{escape(piece)}</code>"))
        else:
            rendered.append(Markup(escape(piece)))
    return Markup("").join(rendered)


def _prose(fragment: Paragraph | Bullet) -> Markup:
    if fragment.pairs is None:
        return Markup(escape(fragment.text))
    return Markup(" — ").join(
        Markup(f"<strong>{escape(label)}:</strong> {escape(value)}")
        for label, value in fragment.pairs
    )


def _marks(markers: tuple[int, ...], *, seen: set[int], titles: dict[int, str]) -> Markup:
    """The superscript markers, each carrying a CSS-only hover preview of its note.

    The ``title`` attribute is written *before* ``href`` deliberately: the marker's link
    stays in-document (``#fn-{n}``), which is what keeps the archived HTML and the PDF
    self-contained, and the notation-agreement test anchors on ``href`` being the final
    attribute.

    Adjacent markers are joined with a superscript comma (gap R7): markers 2 and 3 set
    flush against each other read as twenty-three, in the PDF's text layer above all.
    """
    parts: list[Markup] = []
    for number in markers:
        anchor = f' id="fnref-{number}"' if number not in seen else ""
        seen.add(number)
        hover = titles.get(number, "")
        title = f' title="{escape(hover)}"' if hover else ""
        parts.append(
            Markup(f'<sup class="fn-ref"{anchor}><a{title} href="#fn-{number}">{number}</a></sup>')
        )
    return Markup('<sup class="fn-sep">,</sup>').join(parts)


def _tail(fragment: Paragraph | Bullet) -> str:
    """The last prose character before this fragment's markers land."""
    if fragment.pairs is None:
        return fragment.text
    return fragment.pairs[-1][1] if fragment.pairs else ""


def _joint(tail: str, markers: tuple[int, ...]) -> Markup:
    """A no-break space before a marker that follows a word rather than punctuation.

    "share" against marker 76 printed as "share76" in a live table (gap R7); after a full
    stop the marker sits flush, which is the typographic convention. No-break, so the
    marker cannot be orphaned onto the next line alone.
    """
    if markers and tail and tail[-1].isalnum():
        return Markup("\N{NO-BREAK SPACE}")
    return Markup("")


def _hover(footnote: Footnote, *, style: HouseStyle) -> str:
    """One footnote as the plain sentence its markers show on hover — no markup, no JS."""
    match footnote:
        case CalculationFootnote():
            shown = " ".join(piece for piece in (footnote.value, footnote.unit) if piece)
            period = f" for {footnote.period_label}" if footnote.period_label else ""
            return (
                f"Calculated: {footnote.formula} = {shown}{period}. "
                "Follow the note to walk it back to its inputs."
            )
        case SourceFootnote():
            pieces = [footnote.title]
            if footnote.publisher:
                pieces.append(footnote.publisher)
            pieces.append(f"tier {footnote.tier}")
            pieces.append(f"retrieved {display.date_text(footnote.retrieved, style=style)}")
            return ", ".join(pieces) + ". Follow the note to the excerpt behind it."
        case UnresolvedFootnote():
            return (
                f"Unresolved citation: the cited {footnote.kind_label} is no longer "
                "present. Do not rely on the figure it supports."
            )


# -- Exhibits --------------------------------------------------------------------------------


def _chart(chart: ChartView, *, seen: set[int], titles: dict[int, str]) -> dict[str, object]:
    """One exhibit as template data: the SVG as a data URI, the caption with its markers.

    A data URI rather than inline SVG — see :func:`aer.charts.svg_data_uri`. The caption's
    markers go through the same ``_marks`` as a section's, so an exhibit's provenance links
    exactly the way a paragraph's does.
    """
    return {
        "key": chart.key,
        "title": chart.title,
        "uri": svg_data_uri(chart.svg),
        "caption": Markup(
            f"{escape(chart.caption)}{_joint(chart.caption, chart.markers)}"
            f"{_marks(chart.markers, seen=seen, titles=titles)}"
        ),
        "placeholder": chart.placeholder,
    }


# -- Footnotes as display rows ---------------------------------------------------------------


def _footnote(
    footnote: Footnote, *, job_id: object = None, style: HouseStyle | None = None
) -> dict[str, object]:
    """One footnote as template data; the entry text is built here, escaped.

    ``drill_href`` is the provenance drill-down for this marker — the page that answers
    with the excerpt, its verification state and the artefact digest (or the calculation
    walk). Written into the document itself, so the archived HTML carries the path back
    to its evidence rather than only the evidence's description.
    """
    match footnote:
        case CalculationFootnote():
            # The unit is blank for a dimensionless ratio; joining the present pieces
            # keeps "= 0.4376 (…)" from carrying a stray double space. The period, when
            # the row carries one, dates the figure where the reader resolves it (A54).
            shown = " ".join(piece for piece in (footnote.value, footnote.unit) if piece)
            period = f" for {escape(footnote.period_label)}" if footnote.period_label else ""
            text = Markup(
                f"Calculated: <code>{escape(footnote.formula)}</code> = "
                f"{escape(shown)}{period} "
                f"(<code>{escape(footnote.function_ref)}</code>, code version "
                f"<code>{escape(footnote.code_version_prefix)}</code>)."
            )
        case SourceFootnote():
            pieces = [Markup(escape(footnote.title))]
            if footnote.publisher:
                pieces.append(Markup(escape(footnote.publisher)))
            active = style if style is not None else HouseStyle()
            if footnote.publication_date:
                pieces.append(
                    Markup(
                        f"published {display.date_text(footnote.publication_date, style=active)}"
                    )
                )
            pieces.append(
                Markup(f"retrieved {display.date_text(footnote.retrieved, style=active)}")
            )
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
    drill_href = f"/runs/{job_id}/footnotes/{footnote.number}" if job_id is not None else None
    return {"number": footnote.number, "text": text, "drill_href": drill_href}
