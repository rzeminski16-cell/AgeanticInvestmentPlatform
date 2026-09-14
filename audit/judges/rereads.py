"""Phase 0.1 — ask each recorded judge what one change would move them off "no".

    uv run python -m audit.judges.rereads            # all 18
    uv run python -m audit.judges.rereads --limit 2  # a two-read dry run first

Eighteen expensive reads produced a pile of complaints and no ordering over them. This asks
the same judges, over the same documents, the one question the original rubric did not:
**what would you need to see?** It is judge tokens only — no live run, no database, no worker
— and it converts complaints into a ranked backlog before the delivery plan commits ~80
sessions to an ordering inferred from post-hoc prose.

Each judge is handed its own recorded read verbatim and the document it read, and is asked
for a ranked list of changes with an effort guess against each. The aggregation is
deterministic: the model proposes changes and ranks them; :mod:`audit.judges.backlog` counts
and orders. Nothing here decides what gets built.

Outside the platform boundary on purpose (`audit/` may import the SDK), and it writes no
``agent_runs`` and no ``costs`` row: this is not a platform model call and must not pollute
the run ledger or the monthly cap. Its own spend goes to the audit ledger.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

import anthropic

from aer.config import load_settings
from aer.providers.costs import price_usage
from aer.providers.protocol import Usage
from audit.ledger import Ledger

MODEL: Final = "claude-opus-5"
EFFORT: Final = "medium"
MAX_TOKENS: Final = 8_000
CONCURRENCY: Final = 4

OUT_DIR: Final = Path("audit/out/judges")
READS_FILE: Final = OUT_DIR / "reads.json"
REREADS_FILE: Final = OUT_DIR / "rereads.json"

SYSTEM: Final = """You are one of three independent judges who read equity research \
documents blind, under a fixed rubric, as part of an audit in September 2026. You are being \
handed your own recorded read and the document it was about.

You are not being asked to re-judge the document, and you must not revise your recorded \
verdict. You are being asked the one question the original rubric did not ask: what would \
have to be different.

Answer as the lens you read under. The operator is a private investor deciding whether to \
open or add to a position. The reader is somebody handed the document who must be able to \
trust a number without the interface. The sceptic is somebody checking the work, who needs to \
get from any figure to a filing and from any objection to what it rests on.

Be concrete about what would appear on the page. "State a view" is not an answer; "a stated \
base-case range with the method named, set against the current share price, in the executive \
summary" is. Judge changes by how much they would move *you*, not by how hard they sound to \
build.

Reply with a single JSON object and nothing else."""

SCHEMA: Final = """Reply with exactly this JSON shape:

{
  "single_change": "The ONE change that would move you most. One or two sentences, concrete \
about what would appear on the page and where.",
  "to_prefer_it": "What you would need to see before you would prefer this document to the \
alternative you were comparing it against.",
  "ranked_changes": [
    {
      "change": "concrete, one sentence",
      "touches": "the section or surface it changes",
      "why_it_moves_you": "one sentence",
      "effort_guess": "small" | "medium" | "large",
      "category": "one of: states_a_view | segment_and_breakdown | guidance_and_recent \
| named_competitors | bear_case_with_a_number | internal_contradiction | valuation_shown \
| price_and_multiples | excerpt_and_evidence | adversary_quality | length_and_readability \
| provenance | other"
    }
  ],
  "if_all_were_made": "yes" | "partly" | "no",
  "what_would_still_be_missing": "one sentence, or empty if nothing"
}

Give between three and five ranked changes, most-moving first."""


@dataclass
class Reread:
    subject: str
    doc: str
    lens: str
    identity: str
    path: str
    answer: dict[str, Any]
    usage: dict[str, int]
    cost_gbp: Decimal


@dataclass
class Run:
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    rereads: list[Reread] = field(default_factory=list)
    failures: list[dict[str, str]] = field(default_factory=list)

    @property
    def cost_gbp(self) -> Decimal:
        return sum((r.cost_gbp for r in self.rereads), Decimal("0"))


def _prompt(read: dict[str, Any], document: str) -> str:
    """The judge's own read, then the document, then the question."""
    recorded = json.dumps(read["rubric"], indent=2, ensure_ascii=False)
    return (
        f"Your lens: **{read['lens']}**.\n\n"
        f"## Your recorded read, verbatim\n\n```json\n{recorded}\n```\n\n"
        f"## The document you read\n\n{document}\n\n"
        f"## The question\n\n{SCHEMA}"
    )


def _usage_of(message: Any) -> dict[str, int]:
    usage = message.usage
    return {
        "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
        "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
        "cache_read_input_tokens": int(getattr(usage, "cache_read_input_tokens", 0) or 0),
        "cache_creation_input_tokens": int(getattr(usage, "cache_creation_input_tokens", 0) or 0),
    }


def _answer_of(message: Any) -> dict[str, Any]:
    """The JSON object in the reply, tolerating a fenced block around it."""
    text = "".join(
        block.text for block in message.content if getattr(block, "type", "") == "text"
    ).strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        message_text = f"No JSON object in the reply: {text[:200]!r}"
        raise ValueError(message_text)
    parsed: dict[str, Any] = json.loads(text[start : end + 1])
    return parsed


async def _one(
    client: anthropic.AsyncAnthropic,
    read: dict[str, Any],
    *,
    usd_to_gbp: Decimal,
    semaphore: asyncio.Semaphore,
    run: Run,
) -> None:
    label = f"{read['subject']}/{read['identity']}/{read['lens']}"
    path = Path(read["path"])
    if not path.exists():
        run.failures.append({"read": label, "error": f"document missing: {path}"})
        return
    document = path.read_text(encoding="utf-8")

    async with semaphore:
        try:
            async with client.messages.stream(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=SYSTEM,
                output_config={"effort": EFFORT},
                messages=[{"role": "user", "content": _prompt(read, document)}],
            ) as stream:
                message = await stream.get_final_message()
        # One failed read is not a failed run: it is recorded and the other seventeen stand.
        except Exception as error:
            run.failures.append({"read": label, "error": f"{type(error).__name__}: {error}"})
            return

    try:
        answer = _answer_of(message)
    except (ValueError, json.JSONDecodeError) as error:
        run.failures.append({"read": label, "error": f"unparsable reply: {error}"})
        return

    usage = _usage_of(message)
    lines = price_usage(
        Usage(
            input_tokens=usage["input_tokens"],
            output_tokens=usage["output_tokens"],
            model=MODEL,
            cache_read_tokens=usage["cache_read_input_tokens"],
            cache_write_tokens=usage["cache_creation_input_tokens"],
            stop_reason=str(message.stop_reason),
        ),
        provider="anthropic",
        usd_to_gbp=usd_to_gbp,
    )
    cost_gbp = sum((line.amount_gbp for line in lines), Decimal("0"))
    run.rereads.append(
        Reread(
            subject=read["subject"],
            doc=read["doc"],
            lens=read["lens"],
            identity=read["identity"],
            path=read["path"],
            answer=answer,
            usage=usage,
            cost_gbp=cost_gbp,
        )
    )
    print(f"  {label}: {answer.get('if_all_were_made', '?')}  £{cost_gbp:.3f}")


async def main(limit: int | None = None) -> int:
    settings = load_settings()
    key = settings.require_secret("anthropic_api_key")
    reads = json.loads(READS_FILE.read_text(encoding="utf-8"))["reads"]
    if limit is not None:
        reads = reads[:limit]

    print(f"Re-reading {len(reads)} recorded judge reads at effort={EFFORT}.")
    client = anthropic.AsyncAnthropic(api_key=key, max_retries=3, timeout=900.0)
    semaphore = asyncio.Semaphore(CONCURRENCY)
    run = Run()
    await asyncio.gather(
        *(
            _one(client, read, usd_to_gbp=settings.usd_to_gbp, semaphore=semaphore, run=run)
            for read in reads
        )
    )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    REREADS_FILE.write_text(
        json.dumps(
            {
                "model": MODEL,
                "effort": EFFORT,
                "started_at": run.started_at.isoformat(),
                "finished_at": datetime.now(UTC).isoformat(),
                "cost_gbp": str(run.cost_gbp),
                "rereads": [
                    {
                        "subject": r.subject,
                        "doc": r.doc,
                        "lens": r.lens,
                        "identity": r.identity,
                        "path": r.path,
                        "usage": r.usage,
                        "cost_gbp": str(r.cost_gbp),
                        "answer": r.answer,
                    }
                    for r in run.rereads
                ],
                "failures": run.failures,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    if run.rereads:
        Ledger().record(
            kind="other",
            label="phase0-rereads",
            amount_gbp=run.cost_gbp,
            detail={"reads": len(run.rereads), "model": MODEL, "effort": EFFORT},
        )

    print(f"\n{len(run.rereads)} of {len(reads)} re-read. £{run.cost_gbp:.2f}.")
    for failure in run.failures:
        print(f"  FAILED {failure['read']}: {failure['error']}")
    print(f"Wrote {REREADS_FILE}")
    return 0 if not run.failures else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None, help="re-read only the first N")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.limit)))
