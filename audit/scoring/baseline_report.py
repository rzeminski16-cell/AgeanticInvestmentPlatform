"""Score a baseline note against the same ground truth the platform run is held to.

    uv run python -m audit.scoring.baseline_report audit/out/baseline/msft1 --truth audit/out/msft1/truth.json [--fetch]

What is measured: every numeral the note states, classified by code against the filed
basket; the citations the model attached, by domain tier; and, with ``--fetch``, whether
each cited URL still resolves. The fetch goes through the platform's own `SafeFetcher`
first, which refuses every search-found host by policy (its allowlist for that provider is
empty), so the resolvability check falls back to a bare HEAD request from the audit
package: no content is stored and nothing is cited from it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Final
from urllib.parse import urlparse

import httpx

from audit.scoring.facts import load_truth
from audit.scoring.match import classify_all, summarise
from audit.scoring.numerals import extract_numerals

__all__ = ["score_baseline"]

_URL: Final = re.compile(r"https?://[^\s)\]>\"']+")
_TIERS: Final[tuple[tuple[str, str], ...]] = (
    ("sec.gov", "T1"),
    ("companieshouse.gov.uk", "T1"),
    ("company-information.service.gov.uk", "T1"),
    ("microsoft.com", "T2"),
    ("astrazeneca.com", "T2"),
    ("mtb.com", "T2"),
    ("stlouisfed.org", "T3"),
    ("federalreserve.gov", "T3"),
    ("bls.gov", "T3"),
    ("ons.gov.uk", "T3"),
    ("treasury.gov", "T3"),
    ("nasdaq.com", "T4"),
    ("nyse.com", "T4"),
    ("reuters.com", "T5"),
    ("ft.com", "T5"),
    ("bloomberg.com", "T5"),
    ("wsj.com", "T5"),
    ("cnbc.com", "T5"),
    ("barrons.com", "T5"),
)


def _tier(url: str) -> str:
    host = urlparse(url).netloc.lower()
    for suffix, tier in _TIERS:
        if host == suffix or host.endswith("." + suffix):
            return tier
    return "T6"


async def _resolve(urls: list[str]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=15.0,
        headers={"User-Agent": "readiness-audit/1 (+rzeminski16@gmail.com)"},
    ) as client:
        for url in urls:
            try:
                response = await client.head(url)
                if response.status_code in {403, 405, 501}:
                    response = await client.get(url, headers={"Range": "bytes=0-2048"})
                out[url] = {
                    "status": response.status_code,
                    "final_url": str(response.url),
                    "resolved": response.status_code < 400,
                }
            except httpx.HTTPError as failure:
                out[url] = {"status": None, "error": type(failure).__name__, "resolved": False}
            await asyncio.sleep(0.5)
    return out


def score_baseline(directory: Path, *, truth_path: Path, fetch: bool = False) -> dict[str, Any]:
    note = (directory / "note.md").read_text(encoding="utf-8")
    summary = json.loads((directory / "summary.json").read_text(encoding="utf-8"))
    citations_path = directory / "citations.json"
    citations = (
        json.loads(citations_path.read_text(encoding="utf-8")) if citations_path.exists() else []
    )
    truth = load_truth(truth_path)

    numerals = extract_numerals(note)
    accuracy = summarise(classify_all(numerals, truth))

    urls_in_text = sorted(set(_URL.findall(note)))
    cited_urls = sorted({c.get("url", "") for c in citations if c.get("url")})
    every_url = sorted(set(urls_in_text) | set(cited_urls))
    tiers = Counter(_tier(u) for u in every_url)
    resolvability = asyncio.run(_resolve(every_url)) if fetch and every_url else {}

    score = {
        "label": directory.name,
        "subject": summary.get("subject"),
        "model": summary.get("model"),
        "effort": summary.get("effort"),
        "elapsed_seconds": summary.get("elapsed_seconds"),
        "price_gbp": (summary.get("price") or {}).get("total_gbp"),
        "searches": summary.get("searches"),
        "words": len(note.split()),
        "accuracy": accuracy,
        "citations": {
            "attached_by_model": len(citations),
            "distinct_urls_cited": len(cited_urls),
            "urls_in_text": len(urls_in_text),
            "distinct_urls": len(every_url),
            "by_tier": dict(tiers),
            "primary_share": ((tiers["T1"] + tiers["T2"] + tiers["T3"]) / len(every_url))
            if every_url
            else None,
            "resolved": sum(1 for r in resolvability.values() if r.get("resolved"))
            if resolvability
            else None,
            "unresolved": [u for u, r in resolvability.items() if not r.get("resolved")]
            if resolvability
            else None,
            "fetch_checked": bool(resolvability),
        },
        "sections_present": _sections_present(note),
    }
    (directory / "score-baseline.json").write_text(
        json.dumps(score, indent=2, default=str) + "\n", encoding="utf-8"
    )
    return score


_SECTION_WORDS: Final = (
    "executive summary",
    "investment thesis",
    "business overview",
    "segment",
    "industry",
    "management",
    "historical financial",
    "earnings quality",
    "balance sheet",
    "cash flow",
    "capital allocation",
    "growth outlook",
    "valuation",
    "scenario",
    "key risks",
    "catalyst",
    "prior research",
    "validation",
)


def _sections_present(note: str) -> dict[str, bool]:
    headings = [line.lower() for line in note.splitlines() if line.startswith("#")]
    joined = "\n".join(headings)
    return {word: word in joined for word in _SECTION_WORDS}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("directory", type=Path)
    parser.add_argument("--truth", type=Path, required=True)
    parser.add_argument("--fetch", action="store_true")
    args = parser.parse_args(argv)
    score = score_baseline(args.directory, truth_path=args.truth, fetch=args.fetch)
    print(
        json.dumps(
            {
                "accuracy": {
                    k: v
                    for k, v in score["accuracy"].items()
                    if k not in {"contradictions", "approximations"}
                },
                "citations": score["citations"],
            },
            indent=2,
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
