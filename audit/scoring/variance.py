"""Two runs on one subject: what must agree, and what may differ.

    uv run python -m audit.scoring.variance audit/out/msft1 audit/out/msft2

Every calculation is deterministic Python over stored facts, so two runs over the same
facts and the same code must produce the same figures; a difference is a defect. Everything
a model authored — the plan, the slates, the sections, the challenges — may differ, and the
size of that difference is reported rather than judged.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any

__all__ = ["compare_runs"]


def _export(directory: Path) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((directory / "run-export.json").read_text(encoding="utf-8"))
    return loaded


def _calc_key(row: dict[str, Any]) -> tuple[str, str, str]:
    params = row.get("parameters") or {}
    case = str(params.get("case", "")) if isinstance(params, dict) else ""
    return (str(row.get("name")), str(row.get("period")), case)


def _by_key(rows: list[dict[str, Any]]) -> dict[tuple[str, str, str], list[dict[str, Any]]]:
    """Every row under its key, in the order the run recorded them.

    One key holds many rows: a DCF's five forecast years carry no period label, and a
    sensitivity grid records `present_value` once per cell — 117 of them in MSFT's first
    run. Keeping one row per key (what a dict comprehension does) compared the last cell
    of one run against the last cell of the other and called the rest identical, which is
    how this comparison first reported thirty-one differences that were mostly a grid
    read out of order.
    """
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(_calc_key(row), []).append(row)
    return grouped


def _values(rows: list[dict[str, Any]]) -> Counter[tuple[str, str]]:
    """The (value, unit) pairs a key's rows hold, as a multiset.

    Order inside a key is not the platform's promise and is not stable: `days_outstanding`
    for one year records receivable, inventory and payable days, and two runs emitted the
    same three numbers in a different order. Comparing position by position called all
    three different; what reliability actually claims is that the same numbers came out,
    so the comparison is of multisets, and the ordering difference is reported separately.
    """
    return Counter(
        (str(Decimal(str(row["output_value"]))), str(row.get("output_unit"))) for row in rows
    )


def _step_output(export: dict[str, Any], key: str) -> dict[str, Any]:
    return next((s.get("output") or {} for s in export.get("steps", []) if s["key"] == key), {})


def compare_runs(first: Path, second: Path) -> dict[str, Any]:
    a, b = _export(first), _export(second)
    rows_a, rows_b = a.get("calculations", []), b.get("calculations", [])
    calcs_a, calcs_b = _by_key(rows_a), _by_key(rows_b)
    shared = sorted(set(calcs_a) & set(calcs_b))
    differing = []
    rows_compared = 0
    rows_differing = 0
    for key in shared:
        group_a, group_b = calcs_a[key], calcs_b[key]
        rows_compared += min(len(group_a), len(group_b))
        values_a, values_b = _values(group_a), _values(group_b)
        only_a, only_b = values_a - values_b, values_b - values_a
        unmatched = sum(only_a.values())
        rows_differing += unmatched
        formulae = {row.get("formula") for row in group_a} ^ {row.get("formula") for row in group_b}
        if unmatched or sum(only_b.values()) or formulae or len(group_a) != len(group_b):
            differing.append(
                {
                    "key": key,
                    "rows": (len(group_a), len(group_b)),
                    "rows_differing": unmatched,
                    "formula_changed": sorted(f for f in formulae if f),
                    # Enough to see the shape of the difference without printing a grid.
                    "examples": [
                        {"first": first, "second": second}
                        for first, second in zip(
                            sorted(v for v, _ in only_a.elements()),
                            sorted(v for v, _ in only_b.elements()),
                            strict=False,
                        )
                    ][:3],
                }
            )
    extract_a, extract_b = _step_output(a, "extract"), _step_output(b, "extract")
    assumptions_a = {
        r["name"]: r
        for r in (_step_output(a, "propose_assumptions").get("assumptions") or [])
        if isinstance(r, dict)
    }
    assumptions_b = {
        r["name"]: r
        for r in (_step_output(b, "propose_assumptions").get("assumptions") or [])
        if isinstance(r, dict)
    }
    assumption_deltas = {}
    for name in sorted(set(assumptions_a) | set(assumptions_b)):
        va, vb = assumptions_a.get(name, {}).get("value"), assumptions_b.get(name, {}).get("value")
        try:
            delta = (
                str(Decimal(str(vb)) - Decimal(str(va)))
                if va is not None and vb is not None
                else None
            )
        except Exception:
            delta = None
        assumption_deltas[name] = {"first": va, "second": vb, "delta": delta}
    sections_a = {s["key"]: s for s in a.get("sections", [])}
    sections_b = {s["key"]: s for s in b.get("sections", [])}
    return {
        "first": {
            "label": first.name,
            "job_id": a.get("run", {}).get("job_id"),
            "code_version": a.get("run", {}).get("code_version"),
            "as_of": a.get("subject", {}).get("as_of_date"),
        },
        "second": {
            "label": second.name,
            "job_id": b.get("run", {}).get("job_id"),
            "code_version": b.get("run", {}).get("code_version"),
            "as_of": b.get("subject", {}).get("as_of_date"),
        },
        "calculations": {
            "first": len(calcs_a),
            "second": len(calcs_b),
            "rows": (len(rows_a), len(rows_b)),
            "rows_compared": rows_compared,
            "rows_differing": rows_differing,
            "shared": len(shared),
            "only_first": [list(k) for k in sorted(set(calcs_a) - set(calcs_b))][:40],
            "only_second": [list(k) for k in sorted(set(calcs_b) - set(calcs_a))][:40],
            "differing": differing,
            "identical": len(shared) - len(differing),
        },
        "facts": {
            "chosen": (extract_a.get("facts_chosen"), extract_b.get("facts_chosen")),
            "rejected": (extract_a.get("facts_rejected"), extract_b.get("facts_rejected")),
            "written": (extract_a.get("facts_written"), extract_b.get("facts_written")),
            "unmapped_tags": (
                len(extract_a.get("unmapped_tags", []) or []),
                len(extract_b.get("unmapped_tags", []) or []),
            ),
        },
        "classification": (
            _step_output(a, "classify").get("sector_key"),
            _step_output(b, "classify").get("sector_key"),
        ),
        "assumptions": assumption_deltas,
        "plan": {
            "prior_research_seen": (
                bool((a.get("plan") or {}).get("plan", {}).get("prior_research")),
                bool((b.get("plan") or {}).get("plan", {}).get("prior_research")),
            ),
            "planned_sources": (
                len((a.get("plan") or {}).get("planned_sources", [])),
                len((b.get("plan") or {}).get("planned_sources", [])),
            ),
            "estimated_cost_gbp": (
                (a.get("plan") or {}).get("estimated_cost_gbp"),
                (b.get("plan") or {}).get("estimated_cost_gbp"),
            ),
        },
        "sections": {
            key: {
                "first": sections_a.get(key, {}).get("status"),
                "second": sections_b.get(key, {}).get("status"),
                "confidence": (
                    sections_a.get(key, {}).get("confidence"),
                    sections_b.get(key, {}).get("confidence"),
                ),
            }
            for key in sorted(set(sections_a) | set(sections_b))
        },
        "red_team": {
            "first": len(a.get("disagreements", [])),
            "second": len(b.get("disagreements", [])),
        },
        "spend_gbp": (a.get("run", {}).get("spend_gbp"), b.get("run", {}).get("spend_gbp")),
        "model_calls": (len(a.get("model_calls", [])), len(b.get("model_calls", []))),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("first", type=Path)
    parser.add_argument("second", type=Path)
    args = parser.parse_args(argv)
    result = compare_runs(args.first, args.second)
    out = args.second / f"variance-vs-{args.first.name}.json"
    out.write_text(json.dumps(result, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(result["calculations"] | {"spend": result["spend_gbp"]}, default=str)[:600])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
