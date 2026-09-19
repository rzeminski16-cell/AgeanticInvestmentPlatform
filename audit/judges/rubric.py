"""September's rubric, as code, so the panel can be run again rather than remembered.

`docs/V1.0_Alpha/11-testing-strategy.md` §3.5: *September's panel exists only as its output.
A repeat of it would be a different instrument wearing the same name, so the panel becomes
code before it is used again, and the code is tested offline against September's recorded
reads.*

**Lifted verbatim, and held to that by a test.** Every key here is read off
`docs/plan/readiness-audit-2026-09/judges/reads.json` — the eighteen reads and nine
comparisons the audit actually produced — and `tests/test_judge_rubric.py` diffs the two key
sets in both directions. A rubric that has drifted by one key is a new instrument, and a
round scored on a new instrument cannot be set against September's number, which is the
whole reason ISSUE 2 has a number at all.

Nothing here makes a model call. :mod:`audit.judges.panel` does that, and it is marked
``live_llm`` like every other billable path.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final

__all__ = [
    "AGREEMENT_KEYS",
    "CHOICES",
    "COMPARE_KEYS",
    "FOCUS_QUESTION_KEYS",
    "LENSES",
    "LENS_BRIEFS",
    "READ_KEYS",
    "RECORDED_READS",
    "VERDICT_DIMENSIONS",
    "VERDICT_KEYS",
    "recorded",
]

RECORDED_READS: Final = (
    Path(__file__).resolve().parents[2]
    / "docs"
    / "plan"
    / "readiness-audit-2026-09"
    / "judges"
    / "reads.json"
)

# The three lenses, in the order the audit ran them. Each is a different reader with a
# different reason to care, and the point of three is that a document can satisfy one
# without satisfying another — the console's notes are strong for the reader and weak for
# the sceptic, and a single lens would have reported only one of those.
LENSES: Final[tuple[str, ...]] = ("operator", "reader", "sceptic")

# Lifted from the brief the September reads ran under, and repeated in
# `audit/judges/rereads.py`'s own system prompt. Kept here because the panel and the
# re-reads must describe the same three people.
LENS_BRIEFS: Final[dict[str, str]] = {
    "operator": ("A private investor deciding whether to open or add to a position."),
    "reader": (
        "Somebody handed the document who must be able to trust a number without the interface."
    ),
    "sceptic": (
        "Somebody checking the work, who needs to get from any figure to a filing and "
        "from any objection to what it rests on."
    ),
}

# What one read answers. Fourteen keys, identical across the three lenses.
READ_KEYS: Final[frozenset[str]] = frozenset(
    {
        "bear_case_in_the_body",
        "checklist",
        "first_thing_to_check",
        "focus_questions",
        "numbers_explained",
        "overall",
        "reading_minutes",
        "still_to_look_up",
        "strongest_paragraph",
        "verifiability",
        "view_argued_not_recited",
        "view_stated",
        "weakest_paragraph",
        "would_act_on_it",
    }
)

# Each read answers three focus questions — the subject's own, from the commission — and
# each answer says where in the document it was answered and on what evidence. "Where" is
# what makes a read auditable: a judge who cannot point at the passage has not read it.
FOCUS_QUESTION_KEYS: Final[frozenset[str]] = frozenset(
    {"answered", "question", "where", "with_what_evidence"}
)

# What one comparison carries. `identity` is the assignment's own record of which document
# was which, and is never shown to the judge: see `audit.judges.blinding`.
COMPARE_KEYS: Final[frozenset[str]] = frozenset(
    {"documents", "identity", "lens", "subject", "verdict"}
)

# The six dimensions ISSUE 2's target counts, and the three passages that say why. **The
# six are the target**: "at least 3 of 6 comparisons do not choose the console, and at
# least 2 of 6 choose the platform" counts a round's comparisons — two subjects by three
# lenses in Phase 5 — across these dimensions.
VERDICT_DIMENSIONS: Final[tuple[str, ...]] = (
    "better_answers_the_brief",
    "better_on_the_focus_questions",
    "better_on_recent_developments",
    "better_on_verifiability",
    "better_argued",
    "more_complete_against_the_checklist",
)

VERDICT_KEYS: Final[frozenset[str]] = frozenset(VERDICT_DIMENSIONS) | {
    "what_A_has_that_B_lacks",
    "what_B_has_that_A_lacks",
    "which_would_you_hand_to_a_colleague_and_why",
}

# A dimension is won, lost, or neither. "equal" is a real answer and the scorer counts it
# as *not choosing the console*, which is half of ISSUE 2's target — a judge forced to
# pick between two documents it cannot separate would be inventing the signal.
CHOICES: Final[tuple[str, ...]] = ("A", "B", "equal")

# The agreement block: how three judges reading one document are summarised, one entry per
# subject and side. Seven of the ten are lists parallel to `judges` — each judge's answer in
# the same slot — and `checklist` and `focus` are keyed by the item or question, so the three
# judges' verdicts on *one* checklist line sit together where a disagreement is visible.
#
# Ten, not seven: the first draft of this constant was written off a truncated print and the
# key diff caught the three it had missed on the first run, which is what a diff in both
# directions is for.
AGREEMENT_KEYS: Final[frozenset[str]] = frozenset(
    {
        "judges",
        "act",
        "view_stated",
        "argued",
        "bear",
        "numbers",
        "reading_minutes",
        "check_minutes",
        "checklist",
        "focus",
    }
)


def recorded() -> dict[str, Any]:
    """September's reads, comparisons and agreement, as the audit left them.

    The fixture the offline tests run against. Read rather than vendored: the file is a
    record under `docs/plan/`, and a copy of a record is a second thing to keep true.
    """
    data: dict[str, Any] = json.loads(RECORDED_READS.read_text())
    return data
