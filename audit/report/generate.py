"""The scorecard tables for the findings document, from the audit's own JSON.

Reads ``audit/out/<label>/score-platform.json``, ``score-text.json`` and
``audit/out/baseline/<label>/score-baseline.json`` and prints Markdown. Nothing here scores
anything: it lays out what the scorers wrote, so the document's tables cannot drift from
the numbers.
"""

from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

OUT = Path("audit/out")

__all__ = ["render_scorecard"]


def _load(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return dict(json.loads(path.read_text(encoding="utf-8")))


def _pounds(value: Any) -> str:
    return f"£{Decimal(str(value)):.2f}"


def _minutes(seconds: Any) -> str:
    return f"{Decimal(str(seconds)) / 60:.0f} min"


def _platform_rows(label: str) -> list[str]:
    score = _load(OUT / label / "score-platform.json")
    text = _load(OUT / label / "score-text.json")
    if score is None:
        return [f"| {label} | not run | | | | | | |"]
    r, c, comp, acc = score["reliability"], score["cost"], score["completeness"], score["accuracy"]
    steps_sum = sum(Decimal(str(v)) for v in r["step_elapsed_seconds"].values())
    retries = ", ".join(f"{x['step']}#{x['attempt']}" for x in r["non_gate_retries"]) or "none"
    degraded = ", ".join(d["key"] for d in r["sections_degraded"]) or "none"
    metrics_failed = [k for k, v in acc["metrics"].items() if v["passed"] is False]
    citations = acc["metrics"].get("citation_accuracy", {})
    checkable = text["accuracy"]["checkable"] if text else "-"
    contradicted = text["accuracy"]["contradicted"] if text else "-"
    headline_missing = [k for k, v in comp["headline_figures_present"].items() if not v]
    return [
        f"| {label} | {r['status']} | {_pounds(c['run_spend_gbp'])} | "
        f"{_minutes(steps_sum)} of steps | {comp['sections_generated']}/{comp['sections_total']} "
        f"(degraded: {degraded}) | retries: {retries}; resumes: {r['resume_events']} | "
        f"citations {citations.get('value', '-')} of {citations.get('population', '-')}; "
        f"metrics failing: {', '.join(metrics_failed) or 'none'} | "
        f"{contradicted} contradicted of {checkable} checkable numerals; "
        f"headline missing: {', '.join(headline_missing) or 'none'} |"
    ]


def _baseline_rows(label: str) -> list[str]:
    score = _load(OUT / "baseline" / label / "score-baseline.json")
    if score is None:
        return [f"| {label} | not run | | | | |"]
    acc, cit = score["accuracy"], score["citations"]
    sections = sum(1 for v in score["sections_present"].values() if v)
    return [
        f"| {label} | {_pounds(score['price_gbp'])} | {_minutes(score['elapsed_seconds'])} | "
        f"{score['words']} words; {score['searches']} searches | {sections}/18 section headings | "
        f"{acc['contradicted']} contradicted of {acc['checkable']} checkable numerals | "
        f"{cit['distinct_urls']} URLs, {cit['resolved']} resolved, primary share "
        f"{Decimal(str(cit['primary_share'])):.0%}, citation blocks: {cit['attached_by_model']} |"
    ]


def _gate_rows(label: str) -> list[str]:
    score = _load(OUT / label / "score-platform.json")
    if score is None:
        return []
    return [
        f"| {label} | {g['gate']} | {Decimal(str(g['seconds'])):.0f} s | "
        f"{'approved' if g['approved'] else 'refused'} | {g['findings']} |"
        for g in score["gate_waits"]
    ]


def _cost_rows(label: str) -> list[str]:
    score = _load(OUT / label / "score-platform.json")
    if score is None:
        return []
    rows = []
    for step, item in score["cost"]["estimate_vs_actual"].items():
        rows.append(
            f"| {label} | {step} | {item['estimate']} | {item['actual']} | {item['ratio']} |"
        )
    return rows


def render_scorecard(labels: list[str], baselines: list[str]) -> str:
    lines = [
        "### Platform runs",
        "",
        "| Run | Status | Spend | Wall-clock | Sections | Reliability | Accuracy (the platform's own metrics) | Accuracy (the audit's matcher) |",
        "|---|---|---|---|---|---|---|---|",
        *(row for label in labels for row in _platform_rows(label)),
        "",
        "### Baselines (Opus 5, high effort, server-side web search)",
        "",
        "| Baseline | Price | Elapsed | Size | Structure | Accuracy (the audit's matcher) | Verifiability |",
        "|---|---|---|---|---|---|---|",
        *(row for label in baselines for row in _baseline_rows(label)),
        "",
        "### Gate stops (as the driver met them)",
        "",
        "| Run | Gate | Wait | Decision | Items shown |",
        "|---|---|---|---|---|",
        *(row for label in labels for row in _gate_rows(label)),
        "",
        "### Estimate against actual, per paid step",
        "",
        "| Run | Step | Estimate £ | Actual £ | Ratio |",
        "|---|---|---|---|---|",
        *(row for label in labels for row in _cost_rows(label)),
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", nargs="*", default=["msft1", "msft2", "azn", "mtb"])
    parser.add_argument("--baselines", nargs="*", default=["msft1", "azn", "mtb"])
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)
    text = render_scorecard(args.runs, args.baselines)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
