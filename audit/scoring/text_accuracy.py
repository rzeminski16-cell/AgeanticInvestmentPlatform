"""Every numeral in a report's text, held against the filing (the platform's side).

The baseline scorer does this for a console answer; this is the same reading over the
platform's rendered Markdown, so the two are judged by one rule. The platform's own
figures are also checked by replay and by `cited_figure_agreement`, which this does not
replace: it is the check that does not trust the platform's record of itself.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from audit.scoring.facts import load_truth
from audit.scoring.match import classify_all, summarise
from audit.scoring.numerals import extract_numerals

__all__ = ["score_text"]


def score_text(text_path: Path, *, truth_path: Path) -> dict[str, Any]:
    text = text_path.read_text(encoding="utf-8")
    truth = load_truth(truth_path)
    numerals = extract_numerals(text)
    accuracy = summarise(classify_all(numerals, truth))
    return {
        "text": str(text_path),
        "truth": str(truth_path),
        "words": len(text.split()),
        "numerals": len(numerals),
        "accuracy": accuracy,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("text", type=Path)
    parser.add_argument("--truth", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)
    result = score_text(args.text, truth_path=args.truth)
    out = args.out or args.text.with_name("score-text.json")
    out.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    summary = {k: v for k, v in result["accuracy"].items() if not isinstance(v, list)}
    print(json.dumps({"numerals": result["numerals"], **summary}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
