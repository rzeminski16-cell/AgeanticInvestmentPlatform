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

The round's own comparisons — the six verdicts ISSUE 2 counts — are :func:`compare`, added
when the round ran. It asks each lens which of two blinded documents it would hand to a
colleague, over the rubric's six dimensions, and it is scored against September's nine
recorded comparisons rather than in the abstract: the console won 51 of the 54 dimension
verdicts there, with three *equal* on verifiability and not one dimension going to the
platform. The pre-registration reads the *movement* from that, not the level.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
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
from audit.judges.rubric import LENS_BRIEFS, LENSES, VERDICT_DIMENSIONS, VERDICT_KEYS
from audit.ledger import Ledger

# The judgement model, because the rate is a property of *this* judge reading *these* texts.
# Measuring the blinding with a cheaper reader than the round will use would answer a
# question nobody asked: a tell the workhorse misses and the judgement model sees is a tell
# that survives into the round.
MODEL: Final = "claude-opus-5"
EFFORT: Final = "medium"
MAX_TOKENS: Final = 2_000
# A comparison writes three passages as well as six verdicts, so it needs more room than a
# guess does; too little and the reply is cut off mid-JSON and scores as a failure.
COMPARE_MAX_TOKENS: Final = 6_000
CONCURRENCY: Final = 3

# Anchored to the repository rather than to the working directory, as `rubric.py` already
# anchors the recorded reads: these are committed records, and a relative path makes reading
# one depend on where the process was started from.
ROOT: Final = Path(__file__).resolve().parents[2]
RECORDED: Final = ROOT / "docs" / "plan" / "readiness-audit-2026-09"
PRE_REGISTRATION: Final = ROOT / "docs" / "plan" / "phase-5-pre-registration.json"
OUT_DIR: Final = ROOT / "audit" / "out" / "judges"
IDENTITY_FILE: Final = OUT_DIR / "identity.json"
COMPARE_FILE: Final = OUT_DIR / "compare.json"

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


def _seed(pre_registration: Path = PRE_REGISTRATION) -> int:
    """The assignment seed, from the pre-registration rather than from a constant here.

    A seed written twice is a seed that can disagree with itself after the round, and the
    committed file is the half of the pair that is hashed.
    """
    data = json.loads(pre_registration.read_text(encoding="utf-8"))
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


async def identity(limit: int | None = None, design: RoundDesign | None = None) -> int:
    """Ask each lens which document is the platform's.

    With no design, over September's three pairs under the Phase 5 seed — the gate's own
    measurement. With a round's design, over that round's pairs under its own seed, so the
    rate is measured on the documents the round judged.
    """
    settings = load_settings()
    key = settings.require_secret("anthropic_api_key")
    seed = _seed() if design is None else _seed(design.pre_registration)
    keys = SUBJECTS if design is None else tuple(pair.key for pair in design.pairs)
    assignments = assign(seed=seed, subjects=keys)
    if limit is not None:
        assignments = assignments[:limit]

    asked = [(one, lens) for one in assignments for lens in LENSES]
    print(
        f"Identity guess: {len(asked)} reads over {len(assignments)} blinded pairs, "
        f"seed {seed}, {MODEL} at effort={EFFORT}."
    )
    if design is None:
        documents = {one.subject: _blinded(one.subject) for one in assignments}
    else:
        pairs = {pair.key: pair for pair in design.pairs}
        documents = {one.subject: _pair_documents(pairs[one.subject]) for one in assignments}
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
    identity_file = (
        IDENTITY_FILE if design is None else OUT_DIR / f"identity-phase{design.phase}.json"
    )
    identity_file.write_text(
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
            label=(
                "phase5-gate-identity-guess"
                if design is None
                else f"phase{design.phase}-round-identity-guess"
            ),
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
    print(f"Wrote {identity_file}")
    return 0 if not round_.failures else 1


# --- The round's comparisons -------------------------------------------------------------

# The round's two commissions, and the order `assign` deals them in. Two rather than the
# three above: `mtb` is in the corpus but not in this round, and dealing it a slot would
# change which side is A for the two that are.
ROUND_SUBJECTS: Final[tuple[str, ...]] = ("azn", "msft1")

# The round's platform document is the run commissioned for it, not September's report of
# the same company. That is the whole point of the round, and reading the wrong file would
# produce six comparisons of the corpus against itself.
ROUND_REPORTS: Final = ROOT / "docs" / "plan" / "phase-5-round-2026-09"

# The verdict round's own documents and its hashed readings (delivery plan §11).
ROUND_7_REPORTS: Final = ROOT / "docs" / "plan" / "phase-7-round-2026-09"
PHASE_7_PRE_REGISTRATION: Final = ROOT / "docs" / "plan" / "phase-7-pre-registration.json"

# The two sets a round's comparisons are read in. *Counted* is the set the pre-registration's
# target is taken over; *like for like* is the set whose comparator has not changed since the
# last round, which is the only set in which movement can be attributed to the platform.
COUNTED: Final = "counted"
LIKE_FOR_LIKE: Final = "like_for_like"


@dataclass(frozen=True, slots=True)
class Pair:
    """One pairing each lens is asked to compare: a platform document against a comparator.

    ``key`` is what the assignment is dealt over, so two pairings that share a platform
    document — the round's report against a fresh note and against September's — are two
    keys, each with its own side of the coin.
    """

    key: str
    platform: Path
    baseline: Path
    sets: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RoundDesign:
    """A round's comparisons as its pre-registration fixes them: which pairs, which seed,
    where the record goes, and what the ledger calls the spend."""

    phase: int
    pre_registration: Path
    pairs: tuple[Pair, ...]
    out_file: Path
    ledger_label: str


PHASE_5_ROUND: Final = RoundDesign(
    phase=5,
    pre_registration=PRE_REGISTRATION,
    pairs=tuple(
        Pair(
            key=subject,
            platform=ROUND_REPORTS / subject / "report.md",
            baseline=RECORDED / "baseline" / subject / "note.md",
            sets=(COUNTED, LIKE_FOR_LIKE),
        )
        for subject in ROUND_SUBJECTS
    ),
    out_file=COMPARE_FILE,
    ledger_label="phase5-round-comparisons",
)

# Three pairings, nine comparisons. AZN against September's note is in both sets; MSFT is read
# twice, against the fresh note (the target's set: *with the fresh baseline in the set*, §12)
# and against September's (the set that shows what moved since Phase 5, which used the same
# note).
PHASE_7_ROUND: Final = RoundDesign(
    phase=7,
    pre_registration=PHASE_7_PRE_REGISTRATION,
    pairs=(
        Pair(
            key="azn",
            platform=ROUND_7_REPORTS / "azn" / "report.md",
            baseline=RECORDED / "baseline" / "azn" / "note.md",
            sets=(COUNTED, LIKE_FOR_LIKE),
        ),
        Pair(
            key="msft1",
            platform=ROUND_7_REPORTS / "msft1" / "report.md",
            baseline=ROUND_7_REPORTS / "baseline" / "msft1-fresh" / "note.md",
            sets=(COUNTED,),
        ),
        Pair(
            key="msft1-september",
            platform=ROUND_7_REPORTS / "msft1" / "report.md",
            baseline=RECORDED / "baseline" / "msft1" / "note.md",
            sets=(LIKE_FOR_LIKE,),
        ),
    ),
    out_file=OUT_DIR / "compare-phase7.json",
    ledger_label="phase7-round-comparisons",
)

ROUNDS: Final[dict[int, RoundDesign]] = {5: PHASE_5_ROUND, 7: PHASE_7_ROUND}

# Phase 5's six, the level the verdict round's like-for-like set is read against: every
# comparison handed to the console, and two of 36 dimension verdicts to the platform.
PHASE_5_LEVEL: Final = {"handed_to_the_console": 6, "dimension_verdicts_to_the_platform": 2}

COMPARE_SYSTEM: Final = """You are one of three independent judges comparing two equity \
research documents about the same company, written to the same brief.

Judge them as the lens you are given. Say which document does better on each dimension, and \
answer "equal" when you cannot separate them — "equal" is a real answer and inventing a \
winner you do not believe in is the one thing that would make this worthless.

Judge the documents, not their presentation: both have been put through the same \
neutralising template, so differences in marker style or heading shape are artefacts of \
that and not evidence about either author.

Be concrete. Quote the passages you are relying on.

Reply with a single JSON object and nothing else."""

COMPARE_SCHEMA: Final = """Reply with exactly this JSON shape:

{
  "better_answers_the_brief": "A" | "B" | "equal",
  "better_on_the_focus_questions": "A" | "B" | "equal",
  "better_on_recent_developments": "A" | "B" | "equal",
  "better_on_verifiability": "A" | "B" | "equal",
  "better_argued": "A" | "B" | "equal",
  "more_complete_against_the_checklist": "A" | "B" | "equal",
  "what_A_has_that_B_lacks": "Concrete, quoting the passage. Two or three sentences.",
  "what_B_has_that_A_lacks": "Concrete, quoting the passage. Two or three sentences.",
  "which_would_you_hand_to_a_colleague_and_why": "Name A or B in the first few words, \
then why, in two or three sentences. If you would hand over both, say which one first."
}"""


@dataclass(frozen=True, slots=True)
class Comparison:
    """One lens's verdict on one subject, with the sides it never saw named."""

    subject: str
    lens: str
    identity: dict[str, str]
    verdict: dict[str, Any]
    usage: dict[str, int]
    cost_gbp: Decimal

    def side(self, choice: str) -> str:
        """`A`/`B`/`equal` as `platform`/`baseline`/`equal`, for scoring."""
        return self.identity.get(choice, choice)

    @property
    def dimensions(self) -> dict[str, str]:
        return {name: self.side(str(self.verdict.get(name, ""))) for name in VERDICT_DIMENSIONS}

    @property
    def handed_over(self) -> str:
        """Which side the judge would hand to a colleague, read from its first few words.

        Read from the prose rather than from a field of its own, because the rubric the
        September panel ran under has no such field and adding one would make this a
        different instrument. The schema asks for the letter in the first few words for
        exactly this reason.
        """
        said = str(self.verdict.get("which_would_you_hand_to_a_colleague_and_why", ""))
        head = said[:60]
        found = re.search(r"\b([AB])\b", head)
        return self.side(found.group(1)) if found else "unreadable"


def _pair_documents(pair: Pair) -> dict[str, str]:
    """One pairing's two documents, both blinded, read from their committed copies."""
    return {
        PLATFORM: neutralise(pair.platform.read_text(encoding="utf-8")),
        BASELINE: neutralise(pair.baseline.read_text(encoding="utf-8")),
    }


def _compare_prompt(*, lens: str, assignment: Assignment, documents: Mapping[str, str]) -> str:
    return (
        f"Your lens: **{lens}** — {LENS_BRIEFS[lens]}\n\n"
        f"## Document A\n\n{documents[assignment.a]}\n\n"
        f"## Document B\n\n{documents[assignment.b]}\n\n"
        f"## The question\n\n{COMPARE_SCHEMA}"
    )


async def _one_comparison(
    client: anthropic.AsyncAnthropic,
    *,
    lens: str,
    assignment: Assignment,
    documents: Mapping[str, str],
    usd_to_gbp: Decimal,
    semaphore: asyncio.Semaphore,
    out: list[Comparison],
    failures: list[dict[str, str]],
) -> None:
    label = f"{assignment.subject}/{lens}"
    async with semaphore:
        try:
            async with client.messages.stream(
                model=MODEL,
                max_tokens=COMPARE_MAX_TOKENS,
                system=COMPARE_SYSTEM,
                output_config={"effort": EFFORT},
                messages=[
                    {
                        "role": "user",
                        "content": _compare_prompt(
                            lens=lens, assignment=assignment, documents=documents
                        ),
                    }
                ],
            ) as stream:
                message = await stream.get_final_message()
        except Exception as error:
            failures.append({"comparison": label, "error": f"{type(error).__name__}: {error}"})
            return

    try:
        verdict = _answer_of(message)
    except (ValueError, json.JSONDecodeError) as error:
        failures.append({"comparison": label, "error": f"unparsable reply: {error}"})
        return

    missing = VERDICT_KEYS - set(verdict)
    if missing:
        failures.append({"comparison": label, "error": f"missing keys: {sorted(missing)}"})
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
    one = Comparison(
        subject=assignment.subject,
        lens=lens,
        identity=assignment.identity(),
        verdict=verdict,
        usage=usage,
        cost_gbp=cost_gbp,
    )
    out.append(one)
    chose = one.handed_over
    tally = sum(1 for side in one.dimensions.values() if side == PLATFORM)
    print(
        f"  {label}: hands over the {chose}; {tally}/6 dimensions to the platform  £{cost_gbp:.3f}"
    )


def _scored(comparisons: list[Comparison]) -> dict[str, Any]:
    """What the pre-registration's readings are read from.

    September is the level this measures movement against: nine comparisons, every one
    handed to the console, and 51 of 54 dimension verdicts its way.
    """
    chose_console = sum(1 for c in comparisons if c.handed_over == BASELINE)
    by_dimension: dict[str, dict[str, int]] = {}
    for name in VERDICT_DIMENSIONS:
        tally = {PLATFORM: 0, BASELINE: 0, "equal": 0}
        for c in comparisons:
            side = c.dimensions.get(name, "")
            if side in tally:
                tally[side] += 1
        by_dimension[name] = tally
    return {
        "comparisons": len(comparisons),
        "handed_to_the_console": chose_console,
        "no_longer_chose_the_console": len(comparisons) - chose_console,
        "handed_to_the_platform": sum(1 for c in comparisons if c.handed_over == PLATFORM),
        "by_dimension": by_dimension,
        "dimension_verdicts_to_the_platform": sum(t[PLATFORM] for t in by_dimension.values()),
        "dimension_verdicts_equal": sum(t["equal"] for t in by_dimension.values()),
        "september_for_reference": {
            "comparisons": 9,
            "handed_to_the_console": 9,
            "dimension_verdicts_to_the_platform": 0,
            "dimension_verdicts_equal": 3,
        },
    }


def scored_by_set(comparisons: list[Comparison], design: RoundDesign) -> dict[str, Any]:
    """Each of the design's sets scored on its own: a comparison counts in every set its
    pairing belongs to, and in no other."""
    sets_of = {pair.key: pair.sets for pair in design.pairs}
    names = sorted({name for pair in design.pairs for name in pair.sets})
    return {
        name: _scored([c for c in comparisons if name in sets_of.get(c.subject, ())])
        for name in names
    }


def verdict_round_reading(by_set: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """The quantitative half of the verdict round's readings, exactly as pre-registered.

    ``fixed`` is delivery plan §12's target over the counted six. ``nothing_moved`` is the
    numeric half of the abandonment criterion over the like-for-like six, against Phase 5's
    level; its other half — whether any judge's stated reason changed category — is read
    from the judges' own words and written down beside the quotes, because it is not a
    count. Six answered comparisons in a set are required to read it: fewer, and the reading
    says so rather than being taken over a smaller denominator.
    """
    counted = by_set.get(COUNTED, {})
    like = by_set.get(LIKE_FOR_LIKE, {})
    counted_whole = int(counted.get("comparisons", 0)) == 6
    like_whole = int(like.get("comparisons", 0)) == 6
    fixed = (
        counted_whole
        and int(counted.get("no_longer_chose_the_console", 0)) >= 3
        and int(counted.get("handed_to_the_platform", 0)) >= 2
    )
    nothing_moved = (
        like_whole
        and int(like.get("handed_to_the_console", 0)) == PHASE_5_LEVEL["handed_to_the_console"]
        and int(like.get("dimension_verdicts_to_the_platform", 0))
        <= PHASE_5_LEVEL["dimension_verdicts_to_the_platform"]
    )
    return {
        "counted_six_answered": counted_whole,
        "like_for_like_six_answered": like_whole,
        "fixed": fixed,
        "nothing_moved_since_phase_5": nothing_moved,
        "phase_5_level": dict(PHASE_5_LEVEL),
    }


async def compare(limit: int | None = None, design: RoundDesign = PHASE_5_ROUND) -> int:
    settings = load_settings()
    key = settings.require_secret("anthropic_api_key")
    seed = _seed(design.pre_registration)
    assignments = assign(seed=seed, subjects=tuple(pair.key for pair in design.pairs))
    if limit is not None:
        assignments = assignments[:limit]

    asked = [(one, lens) for one in assignments for lens in LENSES]
    print(
        f"Comparisons: {len(asked)} over {len(assignments)} blinded pairs, seed {seed}, "
        f"{MODEL} at effort={EFFORT}."
    )
    pairs = {pair.key: pair for pair in design.pairs}
    documents = {one.subject: _pair_documents(pairs[one.subject]) for one in assignments}
    client = anthropic.AsyncAnthropic(api_key=key, max_retries=3, timeout=900.0)
    semaphore = asyncio.Semaphore(CONCURRENCY)
    out: list[Comparison] = []
    failures: list[dict[str, str]] = []
    started = datetime.now(UTC)
    await asyncio.gather(
        *(
            _one_comparison(
                client,
                lens=lens,
                assignment=one,
                documents=documents[one.subject],
                usd_to_gbp=settings.usd_to_gbp,
                semaphore=semaphore,
                out=out,
                failures=failures,
            )
            for one, lens in asked
        )
    )

    cost = sum((c.cost_gbp for c in out), Decimal(0))
    score = _scored(out)
    by_set = scored_by_set(out, design)
    reading = verdict_round_reading(by_set) if design.phase == 7 else None
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    design.out_file.write_text(
        json.dumps(
            {
                "phase": design.phase,
                "model": MODEL,
                "effort": EFFORT,
                "seed": seed,
                "started_at": started.isoformat(),
                "finished_at": datetime.now(UTC).isoformat(),
                "cost_gbp": str(cost),
                "asked": len(asked),
                "answered": len(out),
                "score": score,
                "by_set": by_set,
                "reading": reading,
                "pairs": [
                    {
                        "key": pair.key,
                        "platform": str(pair.platform.relative_to(ROOT)),
                        "baseline": str(pair.baseline.relative_to(ROOT)),
                        "sets": list(pair.sets),
                    }
                    for pair in design.pairs
                ],
                "compares": [
                    {
                        "subject": c.subject,
                        "lens": c.lens,
                        "identity": c.identity,
                        "dimensions": c.dimensions,
                        "handed_over": c.handed_over,
                        "usage": c.usage,
                        "cost_gbp": str(c.cost_gbp),
                        "verdict": c.verdict,
                    }
                    for c in out
                ],
                "failures": failures,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    if out:
        Ledger().record(
            kind="other",
            label=design.ledger_label,
            amount_gbp=cost,
            detail={
                "comparisons": len(out),
                "model": MODEL,
                **{k: score[k] for k in ("handed_to_the_console", "no_longer_chose_the_console")},
            },
        )

    print(f"\n{len(out)} of {len(asked)} answered. £{cost:.2f}.")
    print(f"  handed to the console:  {score['handed_to_the_console']} of {len(out)}")
    print(f"  no longer the console:  {score['no_longer_chose_the_console']} of {len(out)}")
    print(
        f"  dimension verdicts to the platform: {score['dimension_verdicts_to_the_platform']} "
        f"(September: 0 of 54); equal: {score['dimension_verdicts_equal']}"
    )
    for name, one in by_set.items():
        print(
            f"  {name}: {one['handed_to_the_platform']} to the platform, "
            f"{one['handed_to_the_console']} to the console, of {one['comparisons']}"
        )
    if reading is not None:
        print(f"  reading: {reading}")
    for failure in failures:
        print(f"  FAILED {failure['comparison']}: {failure['error']}")
    print(f"Wrote {design.out_file}")
    return 0 if not failures else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="The panel's billable reads.")
    parser.add_argument(
        "--identity", action="store_true", help="ask each lens which document is the platform's"
    )
    parser.add_argument(
        "--compare", action="store_true", help="the round's six comparisons, over the rubric"
    )
    parser.add_argument("--limit", type=int, default=None, help="only the first N subjects")
    parser.add_argument(
        "--round",
        type=int,
        choices=sorted(ROUNDS),
        default=None,
        help="the round whose pairs to read; identity defaults to September's corpus",
    )
    args = parser.parse_args()
    if args.identity == args.compare:
        parser.error("pass exactly one of --identity and --compare")
    chosen = ROUNDS[args.round] if args.round is not None else None
    if args.identity:
        raise SystemExit(asyncio.run(identity(args.limit, chosen)))
    raise SystemExit(asyncio.run(compare(args.limit, chosen or PHASE_5_ROUND)))
