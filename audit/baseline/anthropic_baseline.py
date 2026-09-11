"""The console-style baseline: the same brief, answered by Opus 5 with web search.

    uv run python -m audit.baseline.anthropic_baseline msft1 [--max-searches 25]

Deliberately not a platform call. It imports the vendor SDK directly, which nothing under
``src/aer`` outside ``providers/anthropic`` may do, and it writes no ``agent_runs`` or
``costs`` row: the platform's ledger and caps are about the platform's own spending, and a
baseline that counted against them would distort the very comparison it exists for. Its
usage is priced with the platform's own price table so the two sides are like for like,
and recorded in the audit's ledger.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

import anthropic

from aer.config import load_settings
from aer.providers.costs import price_usage, price_web_search
from aer.providers.protocol import Usage
from audit.baseline.brief import FALLBACK_SECTION_TITLES
from audit.ledger import OUT_DIR, Ledger
from audit.subjects import Subject, brief_for, subject_for

__all__ = ["BaselineResult", "run_baseline"]

MODEL: Final = "claude-opus-5"
EFFORT: Final = "high"
MAX_TOKENS: Final = 48_000
# The API may split a long server-tool turn; the documented continuation is to send the
# paused content back unchanged. Bounded so a pathological turn becomes an error.
PAUSE_TURN_LIMIT: Final = 12
SYSTEM: Final = (
    "You are a sell-side equity research analyst writing for a professional reader. Use the "
    "web search tool to read primary sources before you write. Every figure and every "
    "factual claim must carry a citation to a page you read. Do not invent or estimate a "
    "number you did not read; say plainly when something could not be sourced. Write in UK "
    "English. Return the complete note as Markdown."
)


class BaselineResult:
    """What one baseline run produced, and what it cost."""

    def __init__(self, *, subject: Subject, out_dir: Path) -> None:
        self.subject = subject
        self.out_dir = out_dir
        self.text = ""
        self.citations: list[dict[str, Any]] = []
        self.usages: list[dict[str, Any]] = []
        self.responses: list[dict[str, Any]] = []
        self.stop_reasons: list[str] = []
        self.searches = 0
        self.started_at = datetime.now(UTC)
        self.finished_at: datetime | None = None

    def priced(self, usd_to_gbp: Decimal) -> dict[str, Any]:
        lines: list[dict[str, Any]] = []
        total = Decimal(0)
        for usage in self.usages:
            for line in price_usage(
                Usage(
                    input_tokens=usage["input_tokens"],
                    output_tokens=usage["output_tokens"],
                    model=MODEL,
                    cache_read_tokens=usage["cache_read_tokens"],
                    cache_write_tokens=usage["cache_write_tokens"],
                ),
                provider="anthropic",
                usd_to_gbp=usd_to_gbp,
            ):
                lines.append(
                    {
                        "category": line.category.value,
                        "units": str(line.units),
                        "usd": str(line.amount_usd),
                        "gbp": str(line.amount_gbp),
                    }
                )
                total += line.amount_gbp
        search_line = price_web_search(
            self.searches, provider="anthropic", model=MODEL, usd_to_gbp=usd_to_gbp
        )
        if search_line is not None:
            lines.append(
                {
                    "category": search_line.category.value,
                    "units": str(search_line.units),
                    "usd": str(search_line.amount_usd),
                    "gbp": str(search_line.amount_gbp),
                }
            )
            total += search_line.amount_gbp
        return {
            "lines": lines,
            "total_gbp": str(total.quantize(Decimal("0.0001"))),
            "fx_rate": str(usd_to_gbp),
        }


def _usage_of(message: Any) -> dict[str, Any]:
    usage = message.usage
    server = getattr(usage, "server_tool_use", None)
    return {
        "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
        "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
        "cache_read_tokens": int(getattr(usage, "cache_read_input_tokens", 0) or 0),
        "cache_write_tokens": int(getattr(usage, "cache_creation_input_tokens", 0) or 0),
        "web_search_requests": int(getattr(server, "web_search_requests", 0) or 0) if server else 0,
    }


def _collect(message: Any, result: BaselineResult) -> None:
    for block in message.content:
        if getattr(block, "type", "") != "text":
            continue
        result.text += block.text
        for citation in getattr(block, "citations", None) or []:
            if getattr(citation, "type", "") == "web_search_result_location":
                result.citations.append(
                    {
                        "url": getattr(citation, "url", ""),
                        "title": getattr(citation, "title", ""),
                        "cited_text": getattr(citation, "cited_text", ""),
                        "in_text": block.text[:120],
                    }
                )


async def run_baseline(
    subject: Subject,
    *,
    as_of: str,
    section_titles: tuple[str, ...] = FALLBACK_SECTION_TITLES,
    max_searches: int = 25,
    out_root: Path = OUT_DIR,
    label: str | None = None,
) -> BaselineResult:
    settings = load_settings()
    key = settings.require_secret("anthropic_api_key")
    client = anthropic.AsyncAnthropic(api_key=key, max_retries=3, timeout=1800.0)
    label = label or subject.key
    out_dir = out_root / "baseline" / label
    out_dir.mkdir(parents=True, exist_ok=True)
    result = BaselineResult(subject=subject, out_dir=out_dir)

    brief = brief_for(subject, as_of=as_of, section_titles=section_titles)
    request: dict[str, Any] = {
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        "system": SYSTEM,
        "output_config": {"effort": EFFORT},
        "tools": [{"type": "web_search_20260209", "name": "web_search", "max_uses": max_searches}],
        "messages": [{"role": "user", "content": brief}],
    }
    (out_dir / "request.json").write_text(json.dumps(request, indent=2) + "\n", encoding="utf-8")

    for turn in range(PAUSE_TURN_LIMIT + 1):
        async with client.messages.stream(**request) as stream:
            message = await stream.get_final_message()
        payload = message.to_dict()
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, default=str).encode()
        ).hexdigest()
        (out_dir / f"response-{turn}-{digest[:12]}.json").write_text(
            json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8"
        )
        result.responses.append(
            {"turn": turn, "sha256": digest, "stop_reason": message.stop_reason}
        )
        usage = _usage_of(message)
        result.usages.append(usage)
        result.searches += usage["web_search_requests"]
        result.stop_reasons.append(str(message.stop_reason))
        _collect(message, result)
        if message.stop_reason != "pause_turn":
            break
        request["messages"] = [
            *request["messages"],
            {"role": "assistant", "content": message.content},
        ]
    else:
        message_text = f"The baseline for {subject.key} paused more than {PAUSE_TURN_LIMIT} times."
        raise RuntimeError(message_text)

    result.finished_at = datetime.now(UTC)
    priced = result.priced(settings.usd_to_gbp)
    summary = {
        "subject": subject.key,
        "label": label,
        "model": MODEL,
        "effort": EFFORT,
        "as_of": as_of,
        "started_at": result.started_at.isoformat(),
        "finished_at": result.finished_at.isoformat(),
        "elapsed_seconds": (result.finished_at - result.started_at).total_seconds(),
        "turns": len(result.responses),
        "stop_reasons": result.stop_reasons,
        "usage": result.usages,
        "searches": result.searches,
        "citations": len(result.citations),
        "words": len(result.text.split()),
        "price": priced,
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, default=str) + "\n", encoding="utf-8"
    )
    (out_dir / "citations.json").write_text(
        json.dumps(result.citations, indent=2) + "\n", encoding="utf-8"
    )
    (out_dir / "note.md").write_text(result.text, encoding="utf-8")
    Ledger(out_root / "ledger.json").record(
        kind="baseline",
        label=label,
        amount_gbp=Decimal(priced["total_gbp"]),
        detail={
            "searches": result.searches,
            "turns": len(result.responses),
            "words": summary["words"],
        },
    )
    print(json.dumps(summary, indent=2, default=str))
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("subject")
    parser.add_argument("--as-of", default=datetime.now(UTC).date().isoformat())
    parser.add_argument("--max-searches", type=int, default=25)
    parser.add_argument("--label", default=None)
    args = parser.parse_args(argv)
    asyncio.run(
        run_baseline(
            subject_for(args.subject),
            as_of=args.as_of,
            max_searches=args.max_searches,
            label=args.label,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
