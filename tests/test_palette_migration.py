"""The palette migration, held by a ceiling that may only fall.

Roadmap §2.5 asks the migration to end with "a test that fails when a template reintroduces
a raw ramp". A test asserting zero is the obvious way to write that and the wrong one: the
migration is tranches 2 and 4 to 9 of `docs/plan/interface-overhaul.md`, forty-two templates
and 1,837 occurrences, and a red build on day one is a test somebody deletes in week one.

So it ratchets. Every template has a ceiling; a file above its ceiling is a regression and
a file *below* it fails too, naming the number to write down. The second half is what makes
this a ratchet rather than a budget — a template that got better and did not say so leaves
room for it to get worse again silently, which is exactly how 1,334 occurrences became
1,837 while the roadmap still said 1,334.

**Why a grep rather than a parse.** The same enforcement shape ADR 0013 uses for section
keys and ADR 0077 uses for provenance labels: read the file, refuse the string. A Jinja
template is not HTML until it is rendered, the class may sit inside a conditional or a
macro call, and a parser would have to render every branch to see what a grep sees at once.

**What it cannot see.** A class composed at run time — `f"text-{tone}-700"` — is invisible
here, and is also invisible to Tailwind's scanner, so it renders with no colour at all.
`TestNoClassIsComposedAtRunTime` is the guard for that, and it looks at Python and
JavaScript rather than at templates.
"""

from __future__ import annotations

import re
from typing import Final

import pytest

from aer.web.templating import STATIC_DIR, STYLES_DIR, TEMPLATES_DIR

# Tailwind's stock colour ramps. The migration replaces every one of these with a semantic
# token — `canvas / surface / ink / line / verification / decision / success / warning /
# refusal / failure / info / muted` — so that a colour name stops being a lie about what
# renders (roadmap §2.5, and `docs/redesign/01-design-system.md` §2).
_RAMPS: Final = (
    "slate",
    "gray",
    "zinc",
    "neutral",
    "stone",
    "red",
    "orange",
    "amber",
    "yellow",
    "lime",
    "green",
    "emerald",
    "teal",
    "cyan",
    "sky",
    "blue",
    "indigo",
    "violet",
    "purple",
    "fuchsia",
    "pink",
    "rose",
)

# Every utility that takes a colour. Not only `text-` and `bg-`: a `divide-slate-200` or a
# `ring-sky-500` is the same debt and the same lie.
_UTILITIES: Final = (
    "text",
    "bg",
    "border",
    "ring",
    "divide",
    "from",
    "to",
    "via",
    "placeholder",
    "decoration",
    "outline",
    "shadow",
    "accent",
    "caret",
    "fill",
    "stroke",
)

# Deliberately matches a *base* utility, so `hover:bg-slate-100` and `bg-slate-100` count as
# one occurrence of one thing. Counting variant-qualified names instead gives 141 distinct
# strings for the same 1,837 occurrences, and a bare figure quoted without its method is how
# the roadmap's own count came to look like it had moved when it had not.
RAMP_CLASS: Final = re.compile(
    r"\b(?:{utils})-(?:{ramps})-\d{{2,3}}\b".format(
        utils="|".join(_UTILITIES), ramps="|".join(_RAMPS)
    )
)

# The ceiling, per template, as measured on 2026-08-25 at the opening of tranche 0.
#
# **A number here may only ever fall.** Lower it in the same commit that migrates the
# template; the test tells you what to write. The twelve at zero are the shell, the main
# menu and the component macros — everything that arrived with the design tokens — plus
# `portfolio/empty.html`, and they are already at the end state.
#
# A template not in this mapping fails: a new one starts at zero and is added explicitly,
# in the `INSTALLED_TOOLS` idiom of a tuple somebody edits rather than a scan that
# discovers.
CEILING: Final[dict[str, int]] = {
    "_nav.html": 0,
    "_shell/badges.html": 0,
    "_shell/drawer.html": 0,
    "_ui/index.html": 0,
    "_ui/provenance.html": 0,
    "_ui/surfaces.html": 0,
    "assumptions/detail.html": 19,
    "assumptions/list.html": 82,
    "base.html": 0,
    "calculations/detail.html": 27,
    "claims/detail.html": 53,
    "companies/detail.html": 62,
    "index.html": 0,
    "knowledge/graph.html": 12,
    "knowledge/index.html": 44,
    "overview/_attention.html": 0,
    "overview/_missing.html": 0,
    "overview/_run_preview.html": 0,
    "plans/review.html": 85,
    "portfolio/broken.html": 8,
    "portfolio/empty.html": 0,
    "portfolio/index.html": 10,
    "reports/detail.html": 70,
    "reports/index.html": 32,
    "requests/_field.html": 39,
    "requests/_form.html": 18,
    "requests/_form_errors.html": 14,
    "requests/detail.html": 63,
    "requests/edit.html": 4,
    "requests/immutable.html": 14,
    "requests/list.html": 43,
    "requests/new.html": 2,
    "requests/not_found.html": 6,
    "requests/remove.html": 40,
    "runs/assumptions.html": 100,
    "runs/claims.html": 22,
    "runs/console.html": 119,
    "runs/financials.html": 60,
    "runs/footnote.html": 58,
    "runs/peers.html": 44,
    "runs/problem.html": 10,
    "runs/replay.html": 32,
    "runs/review.html": 226,
    "runs/sector.html": 33,
    "runs/sources.html": 58,
    "runs/themes.html": 28,
    "runs/valuation.html": 58,
    "settings/index.html": 34,
    "skills/edit.html": 87,
    "skills/examples.html": 13,
    "skills/import.html": 39,
    "skills/list.html": 36,
    "spend/index.html": 33,
    "tools/index.html": 0,
}

# What the whole tree was when the ratchet was set. Asserted as a total as well as per file,
# because a plan that says "1,837" wants one place that fails when the figure moves.
OPENING_TOTAL: Final = 1_837


def _templates() -> list[str]:
    return sorted(p.relative_to(TEMPLATES_DIR).as_posix() for p in TEMPLATES_DIR.rglob("*.html"))


def _ramps_in(name: str) -> int:
    return len(RAMP_CLASS.findall((TEMPLATES_DIR / name).read_text(encoding="utf-8")))


class TestTheCeilingHoldsAndOnlyFalls:
    """One test per template, so a failure names the file rather than a total."""

    @pytest.mark.parametrize("name", sorted(CEILING))
    def test_a_template_is_at_or_below_its_ceiling(self, name: str) -> None:
        found = _ramps_in(name)
        ceiling = CEILING[name]
        assert found <= ceiling, (
            f"{name} has {found} raw Tailwind ramp classes against a ceiling of {ceiling}. "
            "The palette migration replaces these with semantic tokens; reintroducing one "
            "puts a template back into the dialect roadmap §2.5 exists to remove. Use a "
            "token from `web/styles/app.css`, or — if this is genuinely new debt somebody "
            "has decided to accept — say so in the commit and raise the ceiling knowingly."
        )

    @pytest.mark.parametrize("name", sorted(CEILING))
    def test_a_migrated_template_lowers_its_ceiling(self, name: str) -> None:
        """A file that improved and did not record it leaves room to regress silently.

        This is the half that makes the mapping a ratchet. Without it a template migrated
        from 226 to 4 keeps a ceiling of 226, and the 222 occurrences somebody could add
        back would be invisible — which is the shape of the drift that let the roadmap's
        own figure age by five hundred occurrences.
        """
        found = _ramps_in(name)
        ceiling = CEILING[name]
        assert found >= ceiling, (
            f"{name} is down to {found} raw ramp classes from a ceiling of {ceiling}. "
            f"Good — now lower it: set CEILING[{name!r}] = {found}. Until it is written "
            "down the difference is room to regress into without this test noticing."
        )


class TestTheMappingCoversTheTree:
    def test_every_template_has_a_ceiling(self) -> None:
        """A new template is added here explicitly, at zero.

        Contribute-or-fail, in the shape `db/models/__init__.py` settled for models and
        `web/tools/registry.py` for tools. A scan that discovered its own contents could
        only ever agree with itself.
        """
        missing = sorted(set(_templates()) - set(CEILING))
        assert not missing, (
            f"These templates have no ceiling: {missing}. Add each to CEILING at 0 — a "
            "template written after the design system exists has no reason to carry a raw "
            "ramp, and starting it anywhere above zero is starting it in the old dialect."
        )

    def test_no_ceiling_names_a_template_that_is_gone(self) -> None:
        stale = sorted(set(CEILING) - set(_templates()))
        assert not stale, (
            f"These ceilings name templates that no longer exist: {stale}. Remove them; a "
            "stale entry is an excuse the tree cannot contradict."
        )

    def test_the_total_matches_what_the_plan_says(self) -> None:
        """The one figure quoted across the roadmap, the design brief and the plan.

        `docs/plan/interface-overhaul.md` sizes every tranche off it. A total that moved
        without those documents moving is a plan describing a different codebase.
        """
        total = sum(_ramps_in(name) for name in _templates())
        assert total <= OPENING_TOTAL, (
            f"The tree now holds {total} raw ramp classes against {OPENING_TOTAL} when the "
            "ratchet was set. Something added debt faster than the migration removed it."
        )


# `ink-faint` is gone. It ratcheted from seventeen uses to zero in tranche 2, and this is
# what the ratchet was always going to become: an assertion that it stays gone.
#
# The delivered design system says plainly: "There is no separate 'faint' text token."
# `ink-subtle` replaced it and clears 4.5:1 on every sanctioned background in both schemes,
# where `ink-faint` measured **2.98:1 on canvas and 2.87:1 on sunken** — below even the
# large-text threshold.

_FAINT = re.compile(r"\b[\w-]*-faint\b")


class TestTheRetiredTokenStaysRetired:
    """The way this comes back is somebody lifting a rule from the prototype stylesheet,
    which still defines and uses the old name eighteen times
    (`docs/redesign/05-review-and-corrections.md` D3) and looks authoritative."""

    def test_no_template_names_it(self) -> None:
        offenders = {
            name: found
            for name in _templates()
            if (found := len(_FAINT.findall((TEMPLATES_DIR / name).read_text())))
        }
        assert not offenders, (
            f"These templates name the retired `faint` token: {offenders}. Use `ink-subtle` — "
            "`ink-faint` measured 2.98:1 on canvas and 2.87:1 on sunken, which fails AA for "
            "small text and fails the large-text threshold too."
        )

    def test_the_stylesheet_defines_no_such_token(self) -> None:
        """The other half. A template cannot use a token that does not exist, but a token
        that exists is a token somebody will find and use."""
        source = (STYLES_DIR / "app.css").read_text(encoding="utf-8")

        assert not _FAINT.findall(source), "the stylesheet still defines a `faint` token"


class TestNoClassIsComposedAtRunTime:
    """A class name built at run time exists in no file Tailwind scans, so it has no CSS.

    The failure is silent and looks like a styling bug: the element renders with no colour
    at all. `static/js/console.js` is why `@source "../static/js/*.js"` is in the
    stylesheet — it composes step-status colours, and without that line a failed step got a
    dot with no colour.

    The rule the migration adopts instead is a closed mapping in Python from a semantic
    state key to one complete, literal class string. Then the scanner sees every one.
    """

    # `bg-` + something, `text-` + something: an f-string or a concatenation that begins a
    # utility and does not finish it. Deliberately narrow — a broad "any string starting
    # with a utility prefix" would flag every legitimate literal in the tree.
    _COMPOSED = re.compile(
        r"""["'`](?:{utils})-["'`]?\s*(?:\+|\{{|\$\{{)""".format(utils="|".join(_UTILITIES))
    )

    def test_no_python_composes_a_colour_class(self) -> None:
        offenders = [
            path.relative_to(TEMPLATES_DIR.parent.parent.parent).as_posix()
            for path in (TEMPLATES_DIR.parent).rglob("*.py")
            if self._COMPOSED.search(path.read_text(encoding="utf-8"))
        ]
        assert not offenders, (
            f"These modules build a colour class from parts: {offenders}. Tailwind's "
            "scanner cannot see the result, so the element renders with no colour. Map a "
            "semantic state key to one complete literal class instead."
        )

    def test_no_script_composes_a_colour_class(self) -> None:
        scripts = STATIC_DIR / "js"
        offenders = [
            path.name
            for path in sorted(scripts.glob("*.js"))
            if self._COMPOSED.search(path.read_text(encoding="utf-8"))
        ]
        assert not offenders, (
            f"These scripts build a colour class from parts: {offenders}. They are scanned "
            "by `@source` today, which is a mitigation rather than a licence — the moment "
            "one composes a class the scan cannot resolve, it renders with no colour."
        )


# Every background token whose *lightness* flips between the two schemes. A literal ink on one
# of these is legible in exactly one of them.
_FLIPPING_FILLS: Final[tuple[str, ...]] = (
    "brand",
    "brand-strong",
    "verification",
    "verification-strong",
    "decision",
    "decision-strong",
    "danger-action",
    "danger-action-hover",
    "surface",
    "surface-raised",
    "surface-sunken",
    "canvas",
    "nav-surface",
)

_LITERAL_INK = re.compile(
    r"bg-(" + "|".join(_FLIPPING_FILLS) + r")\b[^\"]*?\btext-(white|black)\b"
)


class TestNoLiteralInkSitsOnAFillThatFlips:
    """The bug tranche 2 shipped for about an hour, and the reason it was not caught.

    Five buttons said `bg-brand ... text-white`. That was correct while `brand` was a mid
    blue in both schemes. The redesign's `verification` is a *pale* teal in dark, so white on
    it measures **1.29:1** — and the hover state 1.14:1. The page still looked deliberate.

    `text-white` cannot flip, and that is the whole defect: a token background changes with
    the scheme and a literal foreground does not, so the pair is legible in one scheme by
    luck. `on-verification`, `on-decision` and `on-danger-action` exist for exactly this and
    are measured in `tests/e2e/test_contrast.py`.

    The contrast test could not see it: that measures the pairings the design *sanctions*,
    and this was a pairing a template invented. This reads the templates instead.

    Ramp fills are deliberately not listed. `bg-sky-700` and `bg-slate-900` do not flip, so
    `text-white` on them is fine — they are on the ramp ratchet for other reasons.
    """

    def test_no_template_pairs_one_with_a_token_fill(self) -> None:
        offenders = {
            name: sorted({f"bg-{fill} + text-{ink}" for fill, ink in found})
            for name in _templates()
            if (found := _LITERAL_INK.findall((TEMPLATES_DIR / name).read_text(encoding="utf-8")))
        }
        assert not offenders, (
            f"These templates put a literal ink on a fill that flips with the scheme: "
            f"{offenders}. Use the matching `on-*` token, which flips with it — white on "
            "`verification` measures 1.29:1 in dark."
        )
