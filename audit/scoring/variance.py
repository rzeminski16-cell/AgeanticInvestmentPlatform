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


def _step_output(export: dict[str, Any], key: str) -> dict[str, Any]:
    return next((s.get("output") or {} for s in export.get("steps", []) if s["key"] == key), {})


def compare_runs(first: Path, second: Path) -> dict[str, Any]:
    a, b = _export(first), _export(second)
    calcs_a = {_calc_key(r): r for r in a.get("calculations", [])}
    calcs_b = {_calc_key(r): r for r in b.get("calculations", [])}
    shared = sorted(set(calcs_a) & set(calcs_b))
    differing = []
    for key in shared:
        ra, rb = calcs_a[key], calcs_b[key]
        same_value = Decimal(str(ra["output_value"])) == Decimal(str(rb["output_value"]))
        same_unit = ra.get("output_unit") == rb.get("output_unit")
        same_formula = ra.get("formula") == rb.get("formula")
        same_code = ra.get("code_version") == rb.get("code_version")
        if not (same_value and same_unit and same_formula):
            differing.append(
                {
                    "key": key,
                    "first": ra["output_value"],
                    "second": rb["output_value"],
                    "unit": (ra.get("output_unit"), rb.get("output_unit")),
                    "same_code_version": same_code,
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
