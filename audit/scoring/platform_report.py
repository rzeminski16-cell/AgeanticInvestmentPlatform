"""Score one platform run from what it recorded: reliability, accuracy, cost, completeness.

    uv run python -m audit.scoring.platform_report audit/out/msft1

Reads the driver's directory — the run export, the steps, the spend, the acceptance readout,
the driver's own event log and the rendered Markdown — and writes ``score-platform.json``
beside them. Nothing here calls a model or the database; the record is the evidence.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

from aer.workflow.workflows.vertical_slice_v1 import build_steps

__all__ = ["score_run"]

# Gate steps run twice by design (pause, then confirm); their second attempt is not a retry.
_GATE_PREFIX: Final = "gate_"
# Headline figures a reader expects on the front page or in the valuation section, as the
# labels the renderer uses. Absence is a completeness datum, not a defect on its own.
_HEADLINES: Final[dict[str, tuple[str, ...]]] = {
    "market_capitalisation": ("market capitalisation", "market cap"),
    "enterprise_value": ("enterprise value",),
    "ev_ebitda": ("ev/ebitda", "ev / ebitda"),
    "pe": ("p/e", "price/earnings", "price to earnings"),
    "dcf_value_per_share": ("value per share", "per share value", "intrinsic value"),
    "scenario_range": ("bear", "bull"),
    "comps_table": ("peer median", "comparable"),
    "wacc": ("wacc", "weighted average cost of capital", "cost of capital"),
    "sensitivity": ("sensitivity",),
}
_COVERAGE_NOTICE: Final = re.compile(
    r"could not be (generated|drafted)|not generated|no evidence|withheld", re.I
)


def _load(directory: Path, name: str) -> Any:
    path = directory / name
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _events(directory: Path, *, job_id: str | None = None) -> list[dict[str, Any]]:
    """The driver's events for one run.

    The log is appended to, so a subject driven twice into the same directory holds both
    runs — M&T was, once the classification defect its first run exposed had been fixed.
    Everything from the newest `commissioned` event onwards is this run's; without the
    trim, the gate waits of the abandoned run were counted beside this one's.
    """
    path = directory / "driver.jsonl"
    if not path.exists():
        return []
    events = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    starts = [
        index
        for index, event in enumerate(events)
        if event.get("event") == "commissioned"
        and (job_id is None or str(event.get("job_id")) == job_id)
    ]
    return events[starts[-1] :] if starts else events


def _estimates() -> dict[str, Decimal]:
    return {step.key: Decimal(step.estimated_cost_gbp) for step in build_steps()}


def _reliability(
    export: dict[str, Any], steps: list[dict[str, Any]], events: list[dict[str, Any]]
) -> dict[str, Any]:
    failed = [s for s in steps if s["status"] == "FAILED"]
    retried = [s for s in steps if s["attempt"] > 0 and not s["step"].startswith(_GATE_PREFIX)]
    gate_second_runs = [s for s in steps if s["attempt"] > 0 and s["step"].startswith(_GATE_PREFIX)]
    sections = export.get("sections", [])
    by_status = Counter(str(s.get("status", "")).lower() for s in sections)
    degraded = [
        {"key": s["key"], "note": s.get("note"), "confidence": s.get("confidence")}
        for s in sections
        if s.get("note")
    ]
    resumes = [
        e
        for e in events
        if e.get("event")
        in {"run.failed", "recovery.resumed_via_service", "recovery.resume_refused"}
    ]
    stops = [
        e
        for e in events
        if e.get("event")
        in {
            "budget.raised",
            "budget.stop",
            "worker.silent",
            "gate.seal_drift",
            "gate.blocked_after_approval",
        }
    ]
    draft = next((s for s in export.get("steps", []) if s["key"] == "draft"), None)
    refusals: Counter[str] = Counter()
    attempts_per_section: dict[str, int] = {}
    lost: list[str] = []
    if draft and isinstance(draft.get("output"), dict):
        for outcome in draft["output"].get("builtin_sections", []) or []:
            if not isinstance(outcome, dict):
                continue
            key = str(outcome.get("key") or outcome.get("section_key") or "")
            attempts_per_section[key] = int(outcome.get("attempts", 0) or 0)
            for cause in outcome.get("refusal_causes", []) or []:
                refusals[str(cause)] += 1
            if str(outcome.get("status", "")).lower() == "failed":
                lost.append(key)
    calls = export.get("model_calls", [])
    stop_reasons = Counter(str(c.get("stop_reason")) for c in calls)
    return {
        "status": export.get("run", {}).get("status"),
        "steps_total": len(steps),
        "steps_failed": [s["step"] for s in failed],
        "non_gate_retries": [{"step": s["step"], "attempt": s["attempt"]} for s in retried],
        "gate_second_runs": len(gate_second_runs),
        "resume_events": len(resumes),
        "stop_events": [e.get("event") for e in stops],
        "sections_by_status": dict(by_status),
        "sections_degraded": degraded,
        "sections_lost": lost,
        "draft_attempts_per_section": attempts_per_section,
        "draft_refusal_causes": dict(refusals),
        "model_calls": len(calls),
        "model_call_stop_reasons": dict(stop_reasons),
        "wall_clock_seconds": _wall_clock(events),
        "step_elapsed_seconds": {
            s["key"]: s.get("elapsed_seconds") for s in export.get("steps", [])
        },
    }


def _wall_clock(events: list[dict[str, Any]]) -> float | None:
    from datetime import datetime  # noqa: PLC0415

    started = next((e["at"] for e in events if e.get("event") == "commissioned"), None)
    finished = next((e["at"] for e in reversed(events) if e.get("event") == "run.finished"), None)
    if not started or not finished:
        return None
    return (datetime.fromisoformat(finished) - datetime.fromisoformat(started)).total_seconds()


def _gate_waits(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """How long the run sat at each gate between pausing and the policy's decision."""
    from datetime import datetime  # noqa: PLC0415

    waits: list[dict[str, Any]] = []
    paused_at: str | None = None
    for event in events:
        if event.get("event") == "advanced" and event.get("status") == "AWAITING_APPROVAL":
            paused_at = event["at"]
        elif event.get("event") == "gate.decided" and paused_at:
            waits.append(
                {
                    "gate": event.get("gate"),
                    "seconds": (
                        datetime.fromisoformat(event["at"]) - datetime.fromisoformat(paused_at)
                    ).total_seconds(),
                    "approved": event.get("approved"),
                    "findings": len(event.get("findings") or []),
                }
            )
            paused_at = None
    return waits


def _accuracy(export: dict[str, Any], acceptance: dict[str, Any] | None) -> dict[str, Any]:
    metrics = {
        row["metric"]: {
            "value": row.get("value"),
            "threshold": row.get("threshold"),
            "passed": row.get("passed"),
            "population": (row.get("details") or {}).get("population"),
        }
        for row in export.get("evaluations", [])
    }
    counts = export.get("counts", {})
    return {
        "metrics": metrics,
        "metrics_failed": [m for m, r in metrics.items() if r["passed"] is False],
        "metrics_not_exercised": [m for m, r in metrics.items() if r["passed"] is None],
        "citations": counts.get("citations"),
        "citations_verified": counts.get("citations_verified"),
        "claims": counts.get("claims"),
        "numeric_claims": counts.get("numeric_claims"),
        "acceptance_passed": (acceptance or {}).get("passed"),
        "acceptance": {
            c["name"]: {"passed": c["passed"], "measured": c["measured"]}
            for c in (acceptance or {}).get("checks", [])
        },
    }


def _cost(
    export: dict[str, Any], steps: list[dict[str, Any]], spend: dict[str, Any] | None
) -> dict[str, Any]:
    estimates = _estimates()
    per_step = {s["step"]: Decimal(s.get("cost_gbp") or 0) for s in steps}
    total = sum(per_step.values(), Decimal(0))
    calls = export.get("model_calls", [])
    unusable = [
        c
        for c in calls
        if str(c.get("stop_reason")) in {"schema_rejected", "max_tokens", "refusal"}
    ]
    cache = (spend or {}).get("cache") or {}
    read = int(cache.get("read_tokens", 0) or 0)
    fresh = int(cache.get("fresh_tokens", 0) or 0)
    written = int(cache.get("written_tokens", 0) or 0)
    prompt_tokens = read + fresh + written
    return {
        "total_gbp": str(total.quantize(Decimal("0.0001"))),
        "run_spend_gbp": export.get("run", {}).get("spend_gbp"),
        "per_step_gbp": {k: str(v) for k, v in per_step.items() if v},
        "estimate_vs_actual": {
            k: {
                "estimate": str(estimates.get(k, Decimal(0))),
                "actual": str(v),
                "ratio": (
                    str((v / estimates[k]).quantize(Decimal("0.01"))) if estimates.get(k) else None
                ),
            }
            for k, v in per_step.items()
            if v or estimates.get(k)
        },
        "guard_table_total_gbp": str(sum(estimates.values(), Decimal(0))),
        "by_role": (spend or {}).get("by_role"),
        "by_kind": (spend or {}).get("by_kind"),
        "cache_hit_rate": (read / prompt_tokens) if prompt_tokens else None,
        "unusable_replies": [
            {
                "step": c["step"],
                "role": c["role"],
                "stop_reason": c["stop_reason"],
                "output_tokens": c["output_tokens"],
            }
            for c in unusable
        ],
        "effort_requested_vs_applied": Counter(
            f"{c.get('model')}:{c.get('effort')}" for c in calls
        ),
    }


def _completeness(export: dict[str, Any], report_md: str) -> dict[str, Any]:
    sections = export.get("sections", [])
    generated = [s["key"] for s in sections if str(s.get("status", "")).lower() == "generated"]
    lowered = report_md.lower()
    headlines = {name: any(word in lowered for word in words) for name, words in _HEADLINES.items()}
    step_output = {s["key"]: (s.get("output") or {}) for s in export.get("steps", [])}
    extract = step_output.get("extract", {})
    prices = step_output.get("acquire_prices", {})
    comps = step_output.get("comps", {})
    value = step_output.get("value", {})
    return {
        "sections_generated": len(generated),
        "sections_total": len(sections),
        "sections_missing": [s["key"] for s in sections if s["key"] not in generated],
        "headline_figures_present": headlines,
        "coverage_notices_in_report": len(_COVERAGE_NOTICE.findall(report_md)),
        "report_words": len(report_md.split()),
        "footnotes": step_output.get("render", {}).get("footnotes"),
        "facts_chosen": extract.get("facts_chosen"),
        "facts_rejected": extract.get("facts_rejected"),
        "unmapped_tags": len(extract.get("unmapped_tags", []) or []),
        "unmapped_concepts": extract.get("unmapped_concepts", []),
        "refused_tags": len(extract.get("refused_tags", []) or []),
        "mapped_concepts": len(extract.get("mapped_concepts", []) or []),
        "prices_acquired": prices.get("prices"),
        "prices_reason": prices.get("reason"),
        "beta_reason": prices.get("beta_reason"),
        "peers_in_comps": comps.get("peers"),
        "comps_built": comps.get("comps"),
        "comps_exclusions": comps.get("exclusions"),
        "valued": value.get("valued"),
        "value_reason": value.get("reason"),
        "calculations": len(export.get("calculations", [])),
        "source_documents": export.get("counts", {}).get("source_documents"),
        "web_searches_billed": None,
    }


def score_run(directory: Path) -> dict[str, Any]:
    export = _load(directory, "run-export.json") or {}
    steps = _load(directory, "steps.json") or []
    spend = _load(directory, "spend.json")
    acceptance = _load(directory, "acceptance.json")
    events = _events(directory, job_id=str(export.get("run", {}).get("job_id") or "") or None)
    report_path = directory / "report.md"
    report_md = report_path.read_text(encoding="utf-8") if report_path.exists() else ""
    score = {
        "label": directory.name,
        "job_id": export.get("run", {}).get("job_id"),
        "subject": export.get("subject"),
        "reliability": _reliability(export, steps, events),
        "gate_waits": _gate_waits(events),
        "accuracy": _accuracy(export, acceptance),
        "cost": _cost(export, steps, spend),
        "completeness": _completeness(export, report_md),
    }
    kinds = dict((spend or {}).get("by_kind") or [])
    score["completeness"]["web_searches_billed"] = kinds.get("web_search")
    (directory / "score-platform.json").write_text(
        json.dumps(score, indent=2, default=str) + "\n", encoding="utf-8"
    )
    return score


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("directory", type=Path)
    args = parser.parse_args(argv)
    score = score_run(args.directory)
    print(json.dumps({k: score[k] for k in ("label", "job_id")}, indent=2))
    print(
        json.dumps(score["reliability"]["sections_by_status"]),
        json.dumps(score["cost"]["total_gbp"]),
        json.dumps(score["completeness"]["sections_generated"]),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
