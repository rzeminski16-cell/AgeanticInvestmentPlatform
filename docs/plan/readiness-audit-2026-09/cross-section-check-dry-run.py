"""What ADR 0125's check would have raised on the committed run records.

The exports omit prose and the reports omit the ledger, so each half is read from the
artefact that carries it: the denial scan over `report.md`, the calculation pass over
`run-export.json`.

Two honest caveats about the fidelity of each half:

* The calculation pass reads **every** row the run recorded, where the real pass reads only
  the ones the report publishes, so its count is an upper bound.
* The denial scan reads the **rendered Markdown**, where the real scan reads each section's
  structured content. Table rows and list items are therefore dropped here: joined into a
  paragraph by the renderer they make sentences no writer wrote, and the real scan never
  meets them because it reads the fields separately.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from aer.core.figure_names import clauses, denies, mentions, periods_named, phrases_for
from aer.core.figures import plain_decimal

ROOT = Path("docs/plan/readiness-audit-2026-09")
SENTENCES = re.compile(r"(?<=[.!?])(?<!\.[A-Z]\.)(?<!\bNo\.)(?<!\bInc\.)(?<!\bU\.S\.)\s+")


def prose_of(report: str) -> str:
    kept = [
        line
        for line in report.splitlines()
        if line.strip() and not line.lstrip().startswith(("|", "#", "-", "*", ">", "[^"))
    ]
    return " ".join(kept)


def names_and_periods(export: dict) -> dict[str, set[str]]:
    index: dict[str, set[str]] = defaultdict(set)
    for row in export.get("calculations", []):
        index[row["name"]].add(row.get("period") or "")
        for item in row.get("inputs", []):
            source = item.get("source") or {}
            if source.get("kind") == "fact":
                index[item["name"]].add(row.get("period") or "")
    return index


def denials(report: str, index: dict[str, set[str]]) -> list[tuple[str, str]]:
    found = []
    for raw in SENTENCES.split(prose_of(report)):
        sentence = " ".join(raw.split())
        named = periods_named(sentence)
        for clause in clauses(sentence):
            if not denies(clause):
                continue
            for name, periods in sorted(index.items()):
                if not any(mentions(clause, phrase) for phrase in phrases_for(name)):
                    continue
                if named and not (named & periods):
                    continue
                found.append((name, clause))
    return found


def clashes_of(export: dict) -> list[tuple[str, str, list[str]]]:
    grouped: dict[tuple[str, ...], set[str]] = defaultdict(set)
    for row in export.get("calculations", []):
        sources = tuple(
            sorted(
                str((item.get("source") or {}).get("id") or f"{item['name']}={item['value']}")
                for item in row.get("inputs", [])
            )
        )
        grouped[(row["name"], row.get("period") or "", *sources)].add(
            f"{plain_decimal(Decimal(row['output_value']))} {row['output_unit']}"
        )
    return [
        (key[0], key[1] or "-", sorted(values))
        for key, values in sorted(grouped.items())
        if len(values) > 1
    ]


print(f"{'subject':<14} {'calcs':>6} {'clashes':>8} {'denials':>8}")
for subject in sorted(p.name for p in ROOT.iterdir() if p.is_dir()):
    export_path = ROOT / subject / "run-export.json"
    report_path = ROOT / subject / "report.md"
    if not export_path.exists():
        continue
    export = json.loads(export_path.read_text())
    index = names_and_periods(export)
    clashes = clashes_of(export)
    rows = denials(report_path.read_text(), index) if report_path.exists() else None
    print(
        f"{subject:<14} {len(export.get('calculations', [])):>6} {len(clashes):>8} "
        f"{'-' if rows is None else len(rows):>8}"
    )
    for name, period, values in clashes[:4]:
        print(f"    clash  {name} {period}: {', '.join(values)[:110]}")
    for name, clause in (rows or [])[:8]:
        print(f"    denial {name}: {clause[:140]}")
