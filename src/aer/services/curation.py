"""The concept-map curation worksheet — roadmap §2.8's mechanism.

A55 is 175 concepts and 110 segment tags the map cannot place, and it has survived several
passes because it is **judgement over accounting semantics, not a code change**: somebody
who knows what a tag means has to decide what it maps to. What a session can do is prepare
the sitting, and that is all this module does — it reads what the runs already recorded,
aggregates it across every run, ranks it as the gate ranks it, and writes a worksheet.

**Ranked, because the work is batched and never finished in one pass.** An alphabetical
list of 175 element names asks the operator to decide where to start, which is the question
that stops the sitting happening at all. Ranked by the largest share of a mapped line any
run ever saw, the first ten rows are the ten that would most change a report.

**Refused tags are excluded, not listed** (§2.7). They are decisions already taken; putting
them in front of somebody whose whole job in this file is to decide what things mean is
inviting exactly the mapping the refusal exists to prevent.

Nothing here writes to the database and nothing here decides anything. The output is a
document the operator edits; turning their decisions into alias-table entries is a separate,
deliberate act by a person reading what they wrote.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from aer.core.concepts import CANONICAL_CONCEPTS, refusal_reason
from aer.db.models import JobStep

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

__all__ = ["WorksheetRow", "curation_worksheet", "render_worksheet", "worksheet_rows"]

# The step whose recorded output carries the unmapped rows the gate showed.
EXTRACT_STEP = "extract"


@dataclass(frozen=True, slots=True)
class WorksheetRow:
    """One tag to decide about, with everything the decision needs beside it."""

    tag: str
    label: str
    # The largest share of a mapped line this tag reached in any run, as a fraction. `None`
    # where no run could scale it — a state worth showing rather than ranking as zero.
    largest_share: Decimal | None
    # The largest absolute figure any run saw, with its unit and period. Not the latest: a
    # tag's most recent observation can be a quarter, a restatement or a zero.
    largest_value: str
    unit: str
    period_end: str
    observations: int
    # How many runs met this tag. A tag one run saw once is a different decision from one
    # every run meets.
    runs: int

    @property
    def share_text(self) -> str:
        if self.largest_share is None:
            return "—"
        return f"{self.largest_share * 100:.2f}%"


@dataclass(frozen=True, slots=True)
class Worksheet:
    """What a sitting works through, and what it deliberately leaves out."""

    rows: tuple[WorksheetRow, ...] = ()
    # Tags excluded because somebody already refused them (§2.7), with their reasons.
    refused: tuple[tuple[str, str], ...] = ()
    runs_read: int = 0
    concepts: tuple[str, ...] = field(default_factory=tuple)


def _decimal_or_none(raw: Any) -> Decimal | None:
    if raw in (None, ""):
        return None
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError):
        return None


def worksheet_rows(outputs: Iterable[Mapping[str, Any]]) -> Worksheet:
    """Aggregate many runs' recorded unmapped rows into one ranked worksheet.

    Pure: it takes the step outputs and returns the worksheet, so the ranking can be tested
    without a database and the caller decides which runs to read.

    **The largest of everything, across runs.** A tag that was 0.4% of one company's
    revenue and 31% of another's is a 31% decision — the point of the sitting is to find
    the tags that would most change a report, and the biggest thing any run saw is the
    honest answer to that.
    """
    seen: dict[str, dict[str, Any]] = {}
    refused: dict[str, str] = {}
    runs_read = 0

    for output in outputs:
        runs_read += 1
        rows = output.get("unmapped_concepts") or []
        # A run recorded before the detail existed carries only the bare tag list. Those
        # tags still belong in the sitting; they just arrive with nothing to rank them by.
        bare = [{"tag": tag} for tag in output.get("unmapped_tags") or []] if not rows else []
        for row in [*rows, *bare]:
            if not isinstance(row, dict):
                continue
            tag = str(row.get("tag") or "").strip()
            if not tag:
                continue

            # Keyed on the bare element name, which is how `NEVER_MAP` is keyed and what a
            # curator is actually deciding about; the taxonomy prefix stays in the display.
            reason = refusal_reason(tag.split(":")[-1])
            if reason:
                refused[tag] = reason
                continue

            share = _decimal_or_none(row.get("share"))
            value = _decimal_or_none(row.get("value"))
            held = seen.get(tag)
            if held is None:
                seen[tag] = {
                    "tag": tag,
                    "label": str(row.get("label") or ""),
                    "share": share,
                    "value": value,
                    "value_text": str(row.get("value") or ""),
                    "unit": str(row.get("unit") or ""),
                    "period_end": str(row.get("period_end") or ""),
                    "observations": int(row.get("observations") or 0),
                    "runs": 1,
                }
                continue

            held["runs"] += 1
            held["observations"] = max(held["observations"], int(row.get("observations") or 0))
            if not held["label"]:
                held["label"] = str(row.get("label") or "")
            if share is not None and (held["share"] is None or share > held["share"]):
                held["share"] = share
            if value is not None and (held["value"] is None or abs(value) > abs(held["value"])):
                held["value"] = value
                held["value_text"] = str(row.get("value") or "")
                held["unit"] = str(row.get("unit") or "")
                held["period_end"] = str(row.get("period_end") or "")

    rows = [
        WorksheetRow(
            tag=held["tag"],
            label=held["label"],
            largest_share=held["share"],
            largest_value=held["value_text"],
            unit=held["unit"],
            period_end=held["period_end"],
            observations=held["observations"],
            runs=held["runs"],
        )
        for held in seen.values()
    ]
    # Biggest share first, then the tags nothing could scale, alphabetically — the same
    # order the gate sorts by, so the worksheet and the screen agree about what matters.
    rows.sort(key=lambda row: (-(row.largest_share or Decimal(0)), row.tag))

    return Worksheet(
        rows=tuple(rows),
        refused=tuple(sorted(refused.items())),
        runs_read=runs_read,
        concepts=tuple(sorted(CANONICAL_CONCEPTS)),
    )


async def curation_worksheet(session: AsyncSession, *, limit: int | None = None) -> Worksheet:
    """The worksheet over every run this platform has recorded.

    Reads the latest attempt of each run's extract step, which is where the gate's own rows
    were recorded — so the worksheet describes what the operator was actually shown rather
    than a re-derivation that might differ.
    """
    outputs = list(
        await session.scalars(
            select(JobStep.output_ref).where(
                JobStep.step_key == EXTRACT_STEP, JobStep.output_ref.is_not(None)
            )
        )
    )
    worksheet = worksheet_rows([output for output in outputs if isinstance(output, dict)])
    if limit is not None:
        worksheet = Worksheet(
            rows=worksheet.rows[:limit],
            refused=worksheet.refused,
            runs_read=worksheet.runs_read,
            concepts=worksheet.concepts,
        )
    return worksheet


def render_worksheet(worksheet: Worksheet) -> str:
    """The worksheet as Markdown, with a column the operator fills in.

    Markdown rather than CSV because the decision needs the reasoning beside it: "maps to
    nothing, it is a components split" is the answer for a good number of these, and a
    spreadsheet cell is where that sentence goes to die.
    """
    lines = [
        "# Concept-map curation worksheet",
        "",
        f"*Prepared from {worksheet.runs_read} recorded "
        f"{'run' if worksheet.runs_read == 1 else 'runs'}. "
        "Roadmap §2.8 — the sitting, not the answer.*",
        "",
        "**Fill in `Maps to` and `Why`.** Leave `Maps to` blank for a tag that should stay "
        "unmapped and say why: a components split, a footnote disclosure, a measure this "
        "platform has no concept for. A tag that must *never* map belongs in `NEVER_MAP` "
        "in `core/concepts.py` with its reason (§2.7) rather than in this file.",
        "",
        "**Ranked by the largest share of a mapped line any run saw.** The first rows are "
        "the ones that would most change a report; a sitting that works down from the top "
        "and stops is a sitting that did the valuable part.",
        "",
        "| # | Tag | Largest figure | Share | Runs | Maps to | Why |",
        "|---:|---|---|---:|---:|---|---|",
    ]
    for number, row in enumerate(worksheet.rows, start=1):
        figure = f"{row.largest_value} {row.unit}".strip() or "—"
        if row.period_end:
            figure = f"{figure} at {row.period_end}"
        label = f"<br>{row.label}" if row.label else ""
        lines.append(
            f"| {number} | `{row.tag}`{label} | {figure} | {row.share_text} | {row.runs} |  |  |"
        )

    if worksheet.refused:
        lines.extend(
            [
                "",
                "## Already refused — nothing to decide here",
                "",
                "These are excluded from the sitting above. Each is a decision somebody "
                "took and recorded; they are listed so the worksheet is a complete account "
                "of what the runs met, not so they are reconsidered.",
                "",
            ]
        )
        lines.extend(f"- `{tag}` — {reason}" for tag, reason in worksheet.refused)

    lines.extend(
        [
            "",
            "## The vocabulary a tag may map onto",
            "",
            "A tag maps onto one of these or onto nothing. Adding a concept is a separate "
            "decision with its own consequences — every adapter has to be able to reach it.",
            "",
        ]
    )
    lines.extend(_wrapped(worksheet.concepts))
    lines.append("")
    return "\n".join(lines)


def _wrapped(concepts: Sequence[str], *, per_line: int = 4) -> list[str]:
    return [
        "- " + " · ".join(f"`{concept}`" for concept in concepts[start : start + per_line])
        for start in range(0, len(concepts), per_line)
    ]
