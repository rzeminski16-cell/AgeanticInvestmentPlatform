"""Phase 0.1 — turn eighteen re-reads into one ranked backlog.

    uv run python -m audit.judges.backlog

Deterministic. The judges proposed changes and ranked them; this counts and orders, and no
model is called here. A change's weight is ``1 / rank`` summed across the reads that named its
category, so a category named first by four judges outranks one named fifth by eight.

Two cuts are reported separately and must not be averaged together:

* **The platform's own documents** (nine reads) — what would move a judge on the thing this
  plan is changing.
* **The console baselines** (nine reads) — what those judges would want from the comparator,
  which is a different question and is recorded so that a change is not credited to this plan
  when the console would need it too.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Final

REREADS_FILE: Final = Path("audit/out/judges/rereads.json")
BACKLOG_FILE: Final = Path("audit/out/judges/backlog.json")

# Which V1.0 feature answers each category. A category with no feature is a gap in the plan
# and is printed as one rather than quietly dropped.
FEATURES: Final[dict[str, str]] = {
    "states_a_view": "F13 the stated view",
    "internal_contradiction": "F4 recompute-everything · F8 print what exists",
    "segment_and_breakdown": "F7 primary-source depth · F8 dimensioned carve-out (ADR 0118)",
    "guidance_and_recent": "F7 exhibits inside accessions already opened",
    "named_competitors": "F8 name the operator-confirmed peers",
    "bear_case_with_a_number": "F2 the adversary argues the other side",
    "valuation_shown": "F8 the valuation rows that fall off the 40-row cap",
    "price_and_multiples": "F8 the subject's own multiples (open question 2, answered)",
    "excerpt_and_evidence": "F8 print the verified excerpt · F16 the boundary first",
    "adversary_quality": "F2 the adversary, and the seeded-false-challenge test",
    "length_and_readability": "— no feature owns this",
    "provenance": "— already the platform's strongest dimension",
    "other": "— uncategorised",
}


def _ordered(weight: dict[str, float]) -> list[tuple[str, float]]:
    """Heaviest first, then alphabetically so a tie does not reorder between runs."""
    return sorted(weight.items(), key=lambda item: (-item[1], item[0]))


def _weighted(rereads: list[dict[str, Any]]) -> tuple[dict[str, float], dict[str, list[str]]]:
    # 1/rank rather than a count: a category named first by four judges outranks one named
    # fifth by eight, which is the ordering the exercise was run to produce.
    weight: dict[str, float] = defaultdict(float)
    examples: dict[str, list[str]] = defaultdict(list)
    for read in rereads:
        for rank, change in enumerate(read["answer"].get("ranked_changes", []), start=1):
            category = str(change.get("category", "other"))
            weight[category] += 1 / rank
            if rank <= 2:
                examples[category].append(
                    f"{read['subject']}/{read['lens']}: {change.get('change', '')}"
                )
    return weight, examples


def _effort(rereads: list[dict[str, Any]]) -> dict[str, Counter[str]]:
    per: dict[str, Counter[str]] = defaultdict(Counter)
    for read in rereads:
        for change in read["answer"].get("ranked_changes", []):
            per[str(change.get("category", "other"))][str(change.get("effort_guess", "?"))] += 1
    return per


def _table(title: str, rereads: list[dict[str, Any]]) -> list[str]:
    weight, examples = _weighted(rereads)
    efforts = _effort(rereads)
    firsts = Counter(
        str(r["answer"]["ranked_changes"][0].get("category", "other"))
        for r in rereads
        if r["answer"].get("ranked_changes")
    )
    lines = [f"\n## {title} ({len(rereads)} reads)\n"]
    verdicts = Counter(str(r["answer"].get("if_all_were_made", "?")) for r in rereads)
    lines.append(
        "If every change they asked for were made, they would act on it: "
        + ", ".join(f"{k} {v}" for k, v in verdicts.most_common())
        + "\n"
    )
    lines.append("| # | Category | Weight | Named first by | Effort | Answered by |")
    lines.append("|---|---|---|---|---|---|")
    for i, (category, score) in enumerate(_ordered(weight), start=1):
        effort = ", ".join(f"{k} {v}" for k, v in efforts[category].most_common())
        lines.append(
            f"| {i} | {category.replace('_', ' ')} | {score:.2f} | {firsts.get(category, 0)} "
            f"| {effort} | {FEATURES.get(category, '?')} |"
        )
    lines.append("\n**What they actually asked for, in the top two ranks:**\n")
    for category, _ in _ordered(weight)[:5]:
        lines.append(f"- **{category.replace('_', ' ')}**")
        for example in examples[category][:3]:
            lines.append(f"  - {example}")
    return lines


def main() -> int:
    data = json.loads(REREADS_FILE.read_text(encoding="utf-8"))
    rereads = data["rereads"]
    platform = [r for r in rereads if r["identity"] == "platform"]
    baseline = [r for r in rereads if r["identity"] == "baseline"]

    out = [
        "# The ranked backlog, from the judges themselves",
        "",
        f"*{len(rereads)} re-reads of the eighteen recorded September reads, "
        f"at £{float(data['cost_gbp']):.2f}. Phase 0.1 of the V1.0 delivery plan. "
        "The judges proposed and ranked; the counting below is deterministic.*",
        "",
        "**Read the two tables apart.** What a judge wants from the platform's document and "
        "what they want from a console note are different questions, and averaging them would "
        "credit this plan with work the comparator needs too.",
    ]
    out += _table("The platform's own documents", platform)
    out += _table("The console baselines", baseline)

    weight, _ = _weighted(platform)
    BACKLOG_FILE.write_text(
        json.dumps(
            {
                "platform": {k: round(v, 4) for k, v in _ordered(weight)},
                "baseline": {k: round(v, 4) for k, v in _ordered(_weighted(baseline)[0])},
                "verdict_if_all_made": {
                    "platform": dict(
                        Counter(str(r["answer"].get("if_all_were_made")) for r in platform)
                    ),
                    "baseline": dict(
                        Counter(str(r["answer"].get("if_all_were_made")) for r in baseline)
                    ),
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
