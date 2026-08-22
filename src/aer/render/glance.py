"""The front page's numbers: an at-a-glance block from what the run already holds.

Gap R10. The live note's front page carried a title, an as-of date, a currency, a flag
and a disclaimer — nothing a reader could use. This module assembles the block from
stored rows alone: the latest reported figures, the annual revenue history, and the
headline calculated figures. **It shows what exists and stays silent about the rest** —
an empty category is omitted, never apologised for, because the coverage notice already
carries the honest account of what a thin run lacks.

Deterministic by construction: every value is a stored fact or a recorded calculation,
every row carries the ids the renderer footnotes, and the block renders through the same
contract walk as any section — same house style, same citation mechanics, same two
notations.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Final

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.calc.plausibility import FigureScene, impossible_relations
from aer.db.models import Calculation, FinancialFact, Job, ResearchRequest
from aer.services.facts import visible_facts
from aer.services.scope import scope_for_request

__all__ = ["GLANCE_CONTRACT", "GLANCE_TITLE", "Glance", "glance_content"]

_log = structlog.get_logger("aer.render.glance")

GLANCE_TITLE: Final = "At a glance"

# The walk's contract for the block: field order is display order, and the series shape
# of ``annual`` is what renders the history with periods across the top (gap R9).
GLANCE_CONTRACT: Final[dict[str, Any]] = {
    "type": "object",
    "title": GLANCE_TITLE,
    "properties": {
        "latest": {
            "type": "array",
            "title": "Latest reported figures",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "period": {"type": "string"},
                    "value": {"type": "string"},
                    "unit": {"type": "string"},
                    "financial_fact_id": {"type": "string"},
                    "source_document_id": {"type": "string"},
                },
            },
        },
        "annual": {
            "type": "array",
            "title": "Revenue history",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "values": {"type": "array", "items": {"type": "object"}},
                },
            },
        },
        "ratios": {
            "type": "array",
            "title": "Headline figures",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "period": {"type": "string"},
                    "value": {"type": "string"},
                    "unit": {"type": "string"},
                    "calculation_id": {"type": "string"},
                },
            },
        },
    },
}

# The concepts the latest-figures table shows, in display order, each with its label.
# EPS prefers the diluted line and falls back to basic — the S3 convention.
_LATEST_CONCEPTS: Final[tuple[tuple[str, str], ...]] = (
    ("revenue", "Revenue"),
    ("net_income", "Net income"),
    ("earnings_per_share_diluted", "EPS (diluted)"),
    ("earnings_per_share_basic", "EPS (basic)"),
)

# The recorded calculations the headline table shows, latest run of each, in this order.
# Curated rather than "every calculation": forty rows of intermediate arithmetic is a
# working paper, not a front page.
_HEADLINE_CALCULATIONS: Final[tuple[tuple[str, str], ...]] = (
    ("gross_margin", "Gross margin"),
    ("operating_margin", "Operating margin"),
    ("net_margin", "Net margin"),
    ("free_cash_flow", "Free cash flow"),
    ("net_debt", "Net debt"),
    ("wacc", "WACC"),
    ("value_per_share", "Value per share (base)"),
)

# How many fiscal years the revenue strip shows. The same span the history chart draws.
_HISTORY_YEARS: Final = 5

# Fewer years than this is not a history; the latest table already carries the level.
_MINIMUM_HISTORY: Final = 2

_ANNUAL: Final = "FY"


@dataclass(frozen=True, slots=True)
class Glance:
    """The block's outcome: content to render, or the stated reason there is none.

    A refusal is an ordinary outcome, not an exception — the document still assembles,
    the coverage notice carries the reason, and the reader is told rather than shown a
    front page that mixes companies.
    """

    content: dict[str, Any] | None
    refused: str | None = None


def _mixing_refusal(facts: list[FinancialFact], subject_id: uuid.UUID | None) -> str | None:
    """The stated reason to withhold the block, or ``None`` when every figure is the
    subject's.

    Deliberately redundant with the query that feeds it (ADR 0061): the predicate in
    :func:`~aer.services.facts.visible_facts` is two lines a refactor can drop, and this
    block is the first thing a reader sees — the live failure it guards against put three
    issuers' figures on one front page as a single company's quarter. So the rows are
    re-checked at the point of rendering, against the subject rather than against each
    other: a set that agrees on the *wrong* company is refused just as firmly as a mix.
    """
    foreign = sum(1 for fact in facts if fact.company_id != subject_id)
    if not foreign:
        return None
    noun = "figure" if len(facts) == 1 else "figures"
    verb = "belongs" if foreign == 1 else "belong"
    return (
        f"the at-a-glance block was withheld — {foreign} of the {len(facts)} stored "
        f"{noun} offered to it {verb} to a company other than the subject, and a "
        "front page must not mix issuers (ADR 0061)"
    )


def _impossibility_refusal(content: dict[str, Any]) -> str | None:
    """The stated reason to withhold the block when its own figures cannot all be true.

    Gap A61's live failure: a front page carrying revenue of $442m, net income of $818m
    and a net margin of 172.1% — each row a stored fact or recorded calculation, the set
    impossible. The check runs over exactly the rows this block is about to render, so
    the block and the check cannot disagree about what the reader would have seen. Which
    figure is wrong is not decidable here — income above revenue means one of the two is
    mislabelled — so the whole block is withheld with the relations stated, the same
    posture as the mixing refusal above: told, rather than shown a lie.
    """
    latest = {row["label"]: row for row in content.get("latest", [])}
    revenue = latest.get("Revenue")
    income = latest.get("Net income")
    comparable = revenue is not None and income is not None and revenue["unit"] == income["unit"]
    scenes = []
    if comparable and revenue is not None and income is not None:
        scenes.append(
            FigureScene(
                period=str(revenue["period"]),
                revenue=Decimal(str(revenue["value"])),
                net_income=Decimal(str(income["value"])),
            )
        )
    scenes.extend(
        FigureScene(period=str(row["period"]), net_margin=Decimal(str(row["value"])))
        for row in content.get("ratios", [])
        if row["label"] == "Net margin"
    )
    found = impossible_relations(tuple(scenes))
    if not found:
        return None
    stated = "; ".join(item.statement for item in found)
    return (
        "the at-a-glance block was withheld — the figures offered to it cannot all be "
        f"true at once: {stated}. A figure that is traceable is not thereby possible "
        "(ADR 0066)"
    )


async def glance_content(session: AsyncSession, *, job: Job, request: ResearchRequest) -> Glance:
    """The at-a-glance block, empty when the run holds nothing to show, and **withheld
    with the reason stated** when the figures offered to it are not all the subject's."""
    facts = await _consolidated_facts(session, request=request)

    refusal = _mixing_refusal(facts, request.company_id)
    if refusal is not None:
        _log.warning(
            "glance.withheld",
            job_id=str(job.id),
            request_id=str(request.id),
            facts=len(facts),
            reason=refusal,
        )
        return Glance(content=None, refused=refusal)

    content: dict[str, Any] = {}
    latest = _latest_rows(facts)
    if latest:
        content["latest"] = latest
    annual = _annual_series(facts)
    if annual:
        content["annual"] = annual
    ratios = await _headline_rows(session, job=job)
    if ratios:
        content["ratios"] = ratios

    refusal = _impossibility_refusal(content)
    if refusal is not None:
        _log.warning(
            "glance.withheld",
            job_id=str(job.id),
            request_id=str(request.id),
            reason=refusal,
        )
        return Glance(content=None, refused=refusal)
    return Glance(content=content or None)


async def _consolidated_facts(
    session: AsyncSession, *, request: ResearchRequest
) -> list[FinancialFact]:
    rows = await session.scalars(
        # The subject's consolidated figures, and nobody else's (ADR 0061). This block is
        # the first thing a reader sees, and the unscoped version put three issuers' figures
        # on one front page: a revenue, a net income and an earnings per share that could
        # not all belong to the same company, since the last two imply a share count
        # two orders of magnitude from the filer's own.
        visible_facts(scope_for_request(request)).order_by(
            FinancialFact.period_end.desc(), FinancialFact.concept
        )
    )
    return list(rows)


def _latest_rows(facts: list[FinancialFact]) -> list[dict[str, str]]:
    """The most recent reported period's figures, one row per concept.

    The period is the latest ``period_end`` carrying a revenue fact — the anchor line
    every filing reports — and every row comes from that same period end, so the table
    never mixes a fresh revenue line with a stale EPS.
    """
    anchor = next((fact for fact in facts if fact.concept == "revenue"), None)
    if anchor is None:
        return []

    rows: list[dict[str, str]] = []
    eps_shown = False
    for concept, label in _LATEST_CONCEPTS:
        if concept.startswith("earnings_per_share") and eps_shown:
            continue
        fact = next(
            (
                item
                for item in facts
                if item.concept == concept and item.period_end == anchor.period_end
            ),
            None,
        )
        if fact is None:
            continue
        if concept.startswith("earnings_per_share"):
            eps_shown = True
        rows.append(
            {
                "label": label,
                "period": _period_text(fact),
                "value": str(fact.value),
                "unit": fact.unit,
                "financial_fact_id": str(fact.id),
                "source_document_id": str(fact.source_document_id),
            }
        )
    return rows


def _annual_series(facts: list[FinancialFact]) -> list[dict[str, Any]]:
    """The full-year revenue strip as one series row, oldest first (gap R9's shape)."""
    by_year: dict[int, FinancialFact] = {}
    for fact in facts:
        if (
            fact.concept == "revenue"
            and fact.fiscal_period == _ANNUAL
            and fact.fiscal_year is not None
        ):
            by_year.setdefault(fact.fiscal_year, fact)
    kept = [by_year[year] for year in sorted(by_year)][-_HISTORY_YEARS:]
    if len(kept) < _MINIMUM_HISTORY:
        return []
    return [
        {
            "label": "Revenue",
            "values": [
                {
                    "period": _period_text(fact),
                    "value": str(fact.value),
                    "unit": fact.unit,
                    "financial_fact_id": str(fact.id),
                    "source_document_id": str(fact.source_document_id),
                }
                for fact in kept
            ],
        }
    ]


async def _headline_rows(session: AsyncSession, *, job: Job) -> list[dict[str, str]]:
    calculations = list(
        await session.scalars(
            select(Calculation).where(Calculation.job_id == job.id).order_by(Calculation.sequence)
        )
    )
    rows: list[dict[str, str]] = []
    for name, label in _HEADLINE_CALCULATIONS:
        latest = next(
            (calc for calc in reversed(calculations) if calc.name == name and _headline(calc)),
            None,
        )
        if latest is None:
            continue
        rows.append(
            {
                "label": label,
                "period": latest.period_label or "\N{EM DASH}",
                "value": str(latest.output_value),
                "unit": latest.output_unit,
                "calculation_id": str(latest.id),
            }
        )
    return rows


def _headline(calc: Calculation) -> bool:
    """Whether this run of a curated name is the front-page one.

    A ``value_per_share`` row can belong to a bear case or a sensitivity cell; only the
    base case is a headline. Rows recorded without a case tag are taken as-is — the tag
    arrived with the scenarios work, and older runs still deserve a front page.
    """
    case = (calc.parameters or {}).get("case")
    return case is None or case == "base"


def _period_text(fact: FinancialFact) -> str:
    if fact.fiscal_period == _ANNUAL:
        return f"FY{fact.fiscal_year}"
    return f"{fact.fiscal_period} FY{fact.fiscal_year}"
