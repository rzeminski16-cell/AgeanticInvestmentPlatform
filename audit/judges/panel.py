"""The panel's billable half: ask a judge which document came from which system.

    uv run python -m audit.judges.panel --identity              # nine guesses
    uv run python -m audit.judges.panel --identity --limit 1    # one, as a dry run first

`docs/V1.0_Alpha/11-testing-strategy.md` §5 row 7 will not let the measurement round spend
until the blinding has been exercised **with the identity-guess hit rate recorded**, and a
hit rate is not something offline code can produce: it is what a judge does when asked.
:mod:`audit.judges.blinding` builds the blinded pair and scores the answers;
:mod:`audit.judges.rubric` holds the three lenses. This is the part in between, and the only
part that costs money.

**What is being measured is the instrument, not the documents.** September's panel was told
it was reading blind and was not — every tell separated the two sides completely — so the
question here is whether :func:`~audit.judges.blinding.neutralise` closed that. A rate at
chance means the round's comparisons stand on the arguments; materially above it, the
pre-registration already decided in advance that the comparison is reported with a caveat
rather than voided, and the judges' stated reasons name what leaked.

**Why the reason is asked for as well as the guess.** A rate on its own says the blinding
failed without saying where, and the next pass would be guesswork. A judge that says *"A
numbers its sources and B doesn't"* has named the tell, which is the difference between a
measurement and a number. The reasons are the finding whichever way the rate falls: at
chance they should be about substance, and if they are still about presentation the rate
was luck.

Outside the platform boundary on purpose, exactly as :mod:`audit.judges.rereads` is: `audit/`
may import the SDK, and this writes no `agent_runs` and no `costs` row, because it is not a
platform model call and must not reach the run ledger or the monthly cap. Its spend goes to
the audit ledger as an `other` line.

The round's own comparisons — the six verdicts ISSUE 2 counts — are Phase 5's work and land
here beside this when the round runs. This is the gate before it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

import anthropic

from aer.config import load_settings
from aer.providers.costs import price_usage
from aer.providers.protocol import Usage
from audit.judges.blinding import BASELINE, PLATFORM, Assignment, assign, hit_rate, neutralise
from audit.judges.rubric import LENS_BRIEFS, LENSES
from audit.ledger import Ledger

# The judgement model, because the rate is a property of *this* judge reading *these* texts.
# Measuring the blinding with a cheaper reader than the round will use would answer a
# question nobody asked: a tell the workhorse misses and the judgement model sees is a tell
# that survives into the round.
MODEL: Final = "claude-opus-5"
EFFORT: Final = "medium"
MAX_TOKENS: Final = 2_000
CONCURRENCY: Final = 3

# Anchored to the repository rather than to the working directory, as `rubric.py` already
# anchors the recorded reads: these are committed records, and a relative path makes reading
# one depend on where the process was started from.
ROOT: Final = Path(__file__).resolve().parents[2]
RECORDED: Final = ROOT / "docs" / "plan" / "readiness-audit-2026-09"
PRE_REGISTRATION: Final = ROOT / "docs" / "plan" / "phase-5-pre-registration.json"
OUT_DIR: Final = ROOT / "audit" / "out" / "judges"
IDENTITY_FILE: Final = OUT_DIR / "identity.json"

# The three pairs September compared: one subject's platform report against the console note
# written to the same commission.
SUBJECTS: Final[tuple[str, ...]] = ("azn", "msft1", "mtb")

SYSTEM: Final = """You are one of three independent judges reading equity research \
documents under a fixed rubric.

You are being handed two documents about the same company, written to the same brief, and \
asked one question that is not about their quality: which of them was produced by which \
system.

One was produced by an automated research platform — a pipeline that plans, fetches filings, \
computes figures deterministically and renders a report. The other was written by an AI \
assistant researching interactively, with a search tool and no pipeline behind it.

Both have been put through the same neutralising template, so presentation is not meant to \
separate them. Answer from whatever you can actually see. If you genuinely cannot tell, say \
so in `confidence` and still commit to a guess — an abstention scores as no evidence either \
way, which is the one answer that tells nobody anything.

Do not judge which document is better. That is a different question, asked elsewhere.

Reply with a single JSON object and nothing else."""

SCHEMA: Final = """Reply with exactly this JSON shape:

{
  "platform_is": "A" | "B",
  "confidence": "certain" | "likely" | "guessing",
  "what_gave_it_away": "The specific thing in the text that decided it — a phrase, a \
habit, a structure. Quote it if you can. One or two sentences.",
  "about_presentation_or_substance": "presentation" | "substance",
  "second_thing": "The next strongest signal, or empty if there was only one."
}"""


@dataclass(frozen=True, slots=True)
class Guess:
    """One judge's answer for one subject, with the truth it was never shown."""

    subject: str
    lens: str
    guessed: str
    platform_was: str
    confidence: str
    answer: dict[str, Any]
    usage: dict[str, int]
    cost_gbp: Decimal

    @property
    def right(self) -> bool:
        return self.guessed == self.platform_was

    def as_scored(self) -> dict[str, str]:
        """The shape :func:`audit.judges.blinding.hit_rate` counts."""
        return {"guessed": self.guessed, "platform_was": self.platform_was}


@dataclass
class Round:
    seed: int
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    guesses: list[Guess] = field(default_factory=list)
    failures: list[dict[str, str]] = field(default_factory=list)

    @property
    def cost_gbp(self) -> Decimal:
        return sum((one.cost_gbp for one in self.guesses), Decimal(0))


def _seed() -> int:
    """The assignment seed, from the pre-registration rather than from a constant here.

    A seed written twice is a seed that can disagree with itself after the round, and the
    committed file is the half of the pair that is hashed.
    """
    data = json.loads(PRE_REGISTRATION.read_text(encoding="utf-8"))
    return int(data["blinding"]["assignment_seed"])


def _blinded(subject: str) -> dict[str, str]:
    """Both sides of one pair, through the neutraliser."""
    return {
        PLATFORM: neutralise((RECORDED / subject / "report.md").read_text(encoding="utf-8")),
        BASELINE: neutralise(
            (RECORDED / "baseline" / subject / "note.md").read_text(encoding="utf-8")
        ),
    }


def _prompt(*, lens: str, assignment: Assignment, documents: Mapping[str, str]) -> str:
    return (
        f"Your lens: **{lens}** — {LENS_BRIEFS[lens]}\n\n"
        f"## Document A\n\n{documents[assignment.a]}\n\n"
        f"## Document B\n\n{documents[assignment.b]}\n\n"
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
        detail = f"No JSON object in the reply: {text[:200]!r}"
        raise ValueError(detail)
    parsed: dict[str, Any] = json.loads(text[start : end + 1])
    return parsed


async def _one(
    client: anthropic.AsyncAnthropic,
    *,
    lens: str,
    assignment: Assignment,
    documents: Mapping[str, str],
    usd_to_gbp: Decimal,
    semaphore: asyncio.Semaphore,
    round_: Round,
) -> None:
    label = f"{assignment.subject}/{lens}"
    async with semaphore:
        try:
            async with client.messages.stream(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=SYSTEM,
                output_config={"effort": EFFORT},
                messages=[
                    {
                        "role": "user",
                        "content": _prompt(lens=lens, assignment=assignment, documents=documents),
                    }
                ],
            ) as stream:
                message = await stream.get_final_message()
        # One failed guess is not a failed round: it is recorded, and the count the rate is
        # taken over is the count of answers, not the count of questions.
        except Exception as error:
            round_.failures.append({"guess": label, "error": f"{type(error).__name__}: {error}"})
            return

    try:
        answer = _answer_of(message)
        guessed = str(answer["platform_is"]).strip().upper()
    except (ValueError, KeyError, json.JSONDecodeError) as error:
        round_.failures.append({"guess": label, "error": f"unparsable reply: {error}"})
        return
    if guessed not in {"A", "B"}:
        round_.failures.append({"guess": label, "error": f"not a slot: {guessed!r}"})
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
    cost_gbp = sum((line.amount_gbp for line in lines), Decimal(0))
    guess = Guess(
        subject=assignment.subject,
        lens=lens,
        guessed=guessed,
        platform_was="A" if assignment.a == PLATFORM else "B",
        confidence=str(answer.get("confidence", "")),
        answer=answer,
        usage=usage,
        cost_gbp=cost_gbp,
    )
    round_.guesses.append(guess)
    mark = "hit " if guess.right else "miss"
    print(f"  {label}: {mark} ({guess.confidence})  £{cost_gbp:.3f}")


def _by_confidence(guesses: list[Guess]) -> dict[str, dict[str, int]]:
    """Hits and misses at each stated confidence.

    The rate over every guess answers the pre-registration's question. This answers the one
    that comes next: a judge at chance while calling itself *certain* has not been blinded,
    it has been confused, and the two look identical in a single figure.
    """
    tally: dict[str, dict[str, int]] = {}
    for guess in guesses:
        row = tally.setdefault(guess.confidence or "unstated", {"hits": 0, "misses": 0})
        row["hits" if guess.right else "misses"] += 1
    return tally


async def identity(limit: int | None = None) -> int:
    settings = load_settings()
    key = settings.require_secret("anthropic_api_key")
    seed = _seed()
    assignments = assign(seed=seed, subjects=SUBJECTS)
    if limit is not None:
        assignments = assignments[:limit]

    asked = [(one, lens) for one in assignments for lens in LENSES]
    print(
        f"Identity guess: {len(asked)} reads over {len(assignments)} blinded pairs, "
        f"seed {seed}, {MODEL} at effort={EFFORT}."
    )
    documents = {one.subject: _blinded(one.subject) for one in assignments}
    client = anthropic.AsyncAnthropic(api_key=key, max_retries=3, timeout=900.0)
    semaphore = asyncio.Semaphore(CONCURRENCY)
    round_ = Round(seed=seed)
    await asyncio.gather(
        *(
            _one(
                client,
                lens=lens,
                assignment=one,
                documents=documents[one.subject],
                usd_to_gbp=settings.usd_to_gbp,
                semaphore=semaphore,
                round_=round_,
            )
            for one, lens in asked
        )
    )

    rate = hit_rate(one.as_scored() for one in round_.guesses)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    IDENTITY_FILE.write_text(
        json.dumps(
            {
                "model": MODEL,
                "effort": EFFORT,
                "seed": seed,
                "started_at": round_.started_at.isoformat(),
                "finished_at": datetime.now(UTC).isoformat(),
                "cost_gbp": str(round_.cost_gbp),
                "asked": len(asked),
                "answered": len(round_.guesses),
                "hit_rate": str(rate),
                "by_confidence": _by_confidence(round_.guesses),
                "assignment": [{"subject": one.subject, **one.identity()} for one in assignments],
                "guesses": [
                    {
                        "subject": one.subject,
                        "lens": one.lens,
                        "guessed": one.guessed,
                        "platform_was": one.platform_was,
                        "right": one.right,
                        "usage": one.usage,
                        "cost_gbp": str(one.cost_gbp),
                        "answer": one.answer,
                    }
                    for one in round_.guesses
                ],
                "failures": round_.failures,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    if round_.guesses:
        Ledger().record(
            kind="other",
            label="phase5-gate-identity-guess",
            amount_gbp=round_.cost_gbp,
            detail={"guesses": len(round_.guesses), "model": MODEL, "hit_rate": str(rate)},
        )

    print(
        f"\n{len(round_.guesses)} of {len(asked)} answered. "
        f"Hit rate {rate} over {len(round_.guesses)}. £{round_.cost_gbp:.2f}."
    )
    for confidence, row in sorted(_by_confidence(round_.guesses).items()):
        print(f"  {confidence}: {row['hits']} right, {row['misses']} wrong")
    for failure in round_.failures:
        print(f"  FAILED {failure['guess']}: {failure['error']}")
    print(f"Wrote {IDENTITY_FILE}")
    return 0 if not round_.failures else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="The panel's billable reads.")
    parser.add_argument(
        "--identity", action="store_true", help="ask each lens which document is the platform's"
    )
    parser.add_argument("--limit", type=int, default=None, help="only the first N subjects")
    args = parser.parse_args()
    if not args.identity:
        parser.error("nothing to do: pass --identity")
    raise SystemExit(asyncio.run(identity(args.limit)))
