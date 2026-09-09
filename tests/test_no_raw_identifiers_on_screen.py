"""No internal identifier is the words a person reads.

`web/vocabulary.py` exists so that a state resolves to a label in the product's own voice,
and `tests/test_presentation_vocabulary.py` keeps that mapping complete. What neither could
catch is a *template* that goes round it — printing `trigger.kind` or `section.key` straight
into the page — because the mapping can be complete and unused.

That is what the first acceptance pass found. The two headings over the operator's own
review read `material_missing_section` and `low_source_coverage`; the section table listed
`growth_outlook` while `section_definitions.title` held the readable name all along; and
the plan gate — the screen where money is approved — said `custom_section` and `house_view`
with `SKILL_KINDS` sitting one import away.

**This is a ratchet, not a rule with exceptions.** `LEDGER` is what remains, counted per
template, in the shape the palette migration used to go from 1,837 to zero. A count may
fall and may not rise, and a template not in the ledger may not acquire one at all. Each
entry carries the reason it is still there, so working it down is a reading task rather
than an archaeology one.
"""

from __future__ import annotations

import re
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parents[1] / "src" / "aer" / "web" / "templates"

# An expression printing an internal identifier. `reason` is deliberately absent: a
# refusal's reason is a sentence the platform wrote, not an identifier. So is `| in_words`,
# which is the mapping doing its job.
_RAW = re.compile(r"\{\{-?\s*[\w.]*\b(?:kind|key|code|status)\s*\}\}")


def _in_an_attribute(text: str, at: int) -> bool:
    """Whether the expression sits inside a tag rather than in the text a person reads.

    `data-section="{{ section.key }}"` is a test hook and an anchor, not a label. Measured
    over the whole file rather than the line, because a tag whose attributes are one per
    line puts the `<` several lines above the expression.
    """
    before = text[:at]
    return before.rfind("<") > before.rfind(">")


# path -> (how many remain, why). **Only ever fewer.**
LEDGER: dict[str, tuple[int, str]] = {
    "plans/review.html": (
        1,
        "A skill's key, which the operator wrote themselves when they authored it. Their "
        "own word for their own file, and the version and kind beside it now read as words.",
    ),
    "skills/list.html": (
        2,
        "The same operator-authored skill key, as the list's identity and in the toggle's "
        "accessible name.",
    ),
    "skills/import.html": (
        2,
        "The key of the skill being imported, which is what the operator is confirming they "
        "meant to import.",
    ),
    "skills/edit.html": (
        1,
        "The dry run's status. Its own small vocabulary, unmapped — a candidate for "
        "`vocabulary.py` when the skills surface is next opened.",
    ),
    "runs/console.html": (
        1,
        "The step's key beside its `STEP_WORDS` name, as muted secondary text. Deliberate "
        "and documented there: it is what a log line and the worker terminal say, so an "
        "operator reading either needs it reachable.",
    ),
    "runs/review.html": (
        1,
        "The section's key beside its title, on the same principle as the console's, and "
        "for the same reason: `aer diagnose` and `aer rehearse-section` are addressed by "
        "key.",
    ),
    "runs/themes.html": (
        1,
        "A theme's key. Machine-shaped today; it becomes the operator's own word when "
        "operator-authored themes land, and this entry goes with it.",
    ),
    "calculations/detail.html": (
        1,
        "The name of one input to a calculation, from the stored ledger row. Provenance: "
        "the reader is being shown what the formula called it, and renaming that would "
        "break the correspondence with the record.",
    ),
    "reports/detail.html": (
        1,
        "The same, for a report's own provenance listing.",
    ),
    "_ui/signatures.html": (
        1,
        "A lineage node's kind — calculation, fact, assumption. Already ordinary words, and "
        "the taxonomy is what a provenance walk is showing.",
    ),
    "spend/index.html": (
        1,
        "A cost row's kind. Unmapped, and the costs page is not one the acceptance pass "
        "raised; a candidate for the next pass over that surface.",
    ),
}


def _offences() -> dict[str, list[str]]:
    """Every raw identifier reaching a reader, by template."""
    found: dict[str, list[str]] = {}
    for path in sorted(TEMPLATES.rglob("*.html")):
        relative = path.relative_to(TEMPLATES).as_posix()
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        for match in _RAW.finditer(text):
            if _in_an_attribute(text, match.start()):
                continue
            number = text.count("\n", 0, match.start()) + 1
            found.setdefault(relative, []).append(f"{number}: {lines[number - 1].strip()}")
    return found


class TestTheRatchet:
    def test_no_template_outside_the_ledger_prints_an_identifier(self) -> None:
        offences = _offences()
        new = {where: lines for where, lines in offences.items() if where not in LEDGER}
        assert not new, (
            "These templates print an internal identifier where a person reads words:\n"
            + "\n".join(
                f"  {where}\n    " + "\n    ".join(lines) for where, lines in sorted(new.items())
            )
            + "\n\nResolve it through `aer.web.vocabulary` — the `in_words` filter is "
            "registered on every template — or add it to LEDGER with the sentence saying "
            "why the identifier serves the reader better."
        )

    def test_no_ledger_entry_has_grown(self) -> None:
        offences = _offences()
        grown = {
            where: (len(offences.get(where, [])), allowed)
            for where, (allowed, _) in LEDGER.items()
            if len(offences.get(where, [])) > allowed
        }
        assert not grown, f"the ledger only goes down; these rose: {grown}"

    def test_no_ledger_entry_is_stale(self) -> None:
        """A ratchet whose entries no longer match anything has stopped ratcheting."""
        offences = _offences()
        stale = sorted(
            where
            for where, (allowed, _) in LEDGER.items()
            if len(offences.get(where, [])) < allowed
        )
        assert not stale, (
            f"These ledger entries are now smaller than recorded: {stale}. Lower the count "
            "— or delete the entry — so the next reader knows what is left."
        )

    def test_every_entry_says_why(self) -> None:
        thin = sorted(where for where, (_, why) in LEDGER.items() if len(why) < 40)
        assert not thin, f"these ledger entries need a reason a reader can weigh: {thin}"
