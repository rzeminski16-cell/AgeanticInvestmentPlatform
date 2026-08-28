"""The type scale, read back against the table it came from.

A scale transcribed by hand is a scale that drifts. Fifteen tokens, five properties each, is
seventy-five chances to type 550 where the design says 500 — and every one of them renders a
page that looks deliberate, because a weight is only wrong next to the weight it should have
been.

So the design system's own table is the fixture. `docs/redesign/01-design-system.md` §3.2 is
parsed and compared to the utilities in `web/styles/app.css`, which makes the document and the
stylesheet one answer instead of two. If the table changes, this fails and names the row.

**Read from the source stylesheet rather than the compiled one**, unlike `test_fonts.py`.
Tailwind emits a `@utility` only where something it scanned uses the class, and no template
names a type token until tranche 3 — a compiled-output assertion would pass vacuously today
and start meaning something later, which is the worst of both.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

import pytest

from aer.web.templating import STYLES_DIR

SPEC = Path("docs/redesign/01-design-system.md")

# The design system's family column, in the words it uses, mapped to the token the stylesheet
# resolves. Three roles and no fourth: a template that wants a fourth is a template making a
# typographic decision the system already made.
FAMILY_TOKEN: Final[dict[str, str]] = {
    "Barlow": "--font-display",
    "Source Sans 3": "--font-sans",
    "IBM Plex Mono": "--font-data",
}

# Layout increments the scale deliberately excludes. They are type metrics, and a layout that
# borrows one drifts off the rhythm a component at a time.
NOT_LAYOUT: Final = ("10px", "14px", "18px", "28px")


def _specified() -> dict[str, dict[str, str]]:
    """§3.2's table, as a mapping from token name to its five decisions."""
    rows: dict[str, dict[str, str]] = {}
    for line in SPEC.read_text(encoding="utf-8").splitlines():
        found = re.match(
            r"\|\s*`type-([\w-]+)`\s*\|\s*([^|]+?)\s*\|\s*(\d+)\s*/\s*(\d+)\s*\|"
            r"\s*(\d+)\s*\|\s*`?([^|`]+?)`?\s*\|",
            line,
        )
        if found:
            name, family, size, leading, weight, tracking = found.groups()
            rows[name] = {
                "family": FAMILY_TOKEN[family],
                "font-size": f"{size}px",
                "line-height": f"{leading}px",
                "font-weight": weight,
                "letter-spacing": tracking,
            }
    return rows


def _declared() -> dict[str, dict[str, str]]:
    """The `@utility type-*` blocks in the source stylesheet."""
    source = (STYLES_DIR / "app.css").read_text(encoding="utf-8")
    blocks: dict[str, dict[str, str]] = {}
    for name, body in re.findall(r"@utility type-([\w-]+) \{(.*?)\n\}", source, re.S):
        declarations = dict(
            re.findall(r"([\w-]+):\s*([^;]+);", body.replace("var(", "").replace(")", ""))
        )
        blocks[name] = {
            "family": declarations.get("font-family", "").strip(),
            "font-size": declarations.get("font-size", "").strip(),
            "line-height": declarations.get("line-height", "").strip(),
            "font-weight": declarations.get("font-weight", "").strip(),
            "letter-spacing": declarations.get("letter-spacing", "").strip(),
        }
    return blocks


SPECIFIED = _specified()
DECLARED = _declared()


class TestTheScaleIsTheOneThatWasSpecified:
    def test_the_table_was_actually_read(self) -> None:
        """If the parse silently finds nothing, every comparison below passes over an empty
        mapping and this file becomes fifteen tests that assert nothing."""
        assert len(SPECIFIED) == 15, f"parsed {len(SPECIFIED)} rows from the type scale table"

    def test_every_specified_token_exists(self) -> None:
        missing = sorted(set(SPECIFIED) - set(DECLARED))
        assert not missing, f"the type scale is missing: {missing}"

    def test_no_token_exists_that_was_never_specified(self) -> None:
        """A sixteenth size is a typographic decision taken outside the system."""
        extra = sorted(set(DECLARED) - set(SPECIFIED))
        assert not extra, f"these type utilities are in no table: {extra}"

    @pytest.mark.parametrize("name", sorted(SPECIFIED))
    def test_the_token_carries_the_whole_decision(self, name: str) -> None:
        """All five properties, together. Half a decision is how a heading ends up in the
        display face at the body weight — which reads as a mistake nobody can name."""
        assert DECLARED[name] == SPECIFIED[name]


class TestTheFiguresAlign:
    @pytest.mark.parametrize("name", [n for n in SPECIFIED if n.startswith(("data", "eyebrow"))])
    def test_every_data_token_sets_tabular_lining_figures(self, name: str) -> None:
        """A column of figures that does not align is a column nobody can scan, and this is a
        platform whose whole subject is columns of figures."""
        source = (STYLES_DIR / "app.css").read_text(encoding="utf-8")
        block = re.search(rf"@utility type-{re.escape(name)} \{{(.*?)\n\}}", source, re.S)

        assert block is not None
        assert "tabular-nums" in block.group(1)
        assert "lining-nums" in block.group(1)

    def test_the_slashed_zero_is_not_on_by_default(self) -> None:
        """It is for hashes and identifiers, where a zero and an O are a real ambiguity. In
        running figures it is noise, and noise on every number is worse than none."""
        source = (STYLES_DIR / "app.css").read_text(encoding="utf-8")

        assert "slashed-zero" not in source


class TestTheSpacingScaleHasNoTypeMetricsInIt:
    @pytest.mark.parametrize("value", NOT_LAYOUT)
    def test_a_type_metric_is_not_a_layout_increment(self, value: str) -> None:
        """A layout that borrows 14px because a label is 14px drifts off the rhythm one
        component at a time, and nothing ever says when it started."""
        source = (STYLES_DIR / "app.css").read_text(encoding="utf-8")
        spacing = re.findall(r"--space-[\w-]+:\s*([^;]+);", source)

        assert value not in [found.strip() for found in spacing]

    def test_the_scale_is_present_at_all(self) -> None:
        source = (STYLES_DIR / "app.css").read_text(encoding="utf-8")

        assert len(re.findall(r"--space-[\w-]+:", source)) == 15
