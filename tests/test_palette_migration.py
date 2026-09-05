"""Raw Tailwind ramps are gone from the templates. This is the assertion that they stay gone.

Roadmap §2.5 asks the migration to end with "a test that fails when a template reintroduces
a raw ramp". For as long as the migration ran — tranches 2 and 4 to 9 of
`docs/plan/interface-overhaul.md`, forty-two templates and 1,837 occurrences at the opening
count — this file was deliberately not that test but a ratchet: a per-template ceiling that
could only fall, because a red build on day one is a test somebody deletes in week one.
Tranche 9 lowered the last ceiling to zero, and the ratchet has become what it was always
going to become: the plain assertion §2.5 asked for.

The per-template enrolment mapping went with it. While ceilings differed per file, a scan
that discovered its own contents could only agree with itself, so templates were enrolled
explicitly in the `INSTALLED_TOOLS` idiom. Now that the expected figure is the same zero
for every template, discovery is the honest shape: a new template is covered the moment it
exists, and there is no entry to forget.

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

# Tailwind's stock colour ramps. The migration replaced every one of these with a semantic
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
# one occurrence of one thing. Counting variant-qualified names instead gave 141 distinct
# strings for the same 1,837 occurrences, and a bare figure quoted without its method is how
# the roadmap's own count came to look like it had moved when it had not.
RAMP_CLASS: Final = re.compile(
    r"\b(?:{utils})-(?:{ramps})-\d{{2,3}}\b".format(
        utils="|".join(_UTILITIES), ramps="|".join(_RAMPS)
    )
)


def _templates() -> list[str]:
    return sorted(p.relative_to(TEMPLATES_DIR).as_posix() for p in TEMPLATES_DIR.rglob("*.html"))


def _ramps_in(name: str) -> int:
    return len(RAMP_CLASS.findall((TEMPLATES_DIR / name).read_text(encoding="utf-8")))


class TestNoTemplateSpeaksTheOldDialect:
    """One test per template, so a failure names the file rather than a total."""

    @pytest.mark.parametrize("name", _templates())
    def test_a_template_holds_no_raw_ramp(self, name: str) -> None:
        found = _ramps_in(name)
        assert found == 0, (
            f"{name} holds {found} raw Tailwind ramp classes. The migration removed all "
            "1,837 of these across seven tranches; reintroducing one puts a template back "
            "into the dialect roadmap §2.5 existed to remove. Use a token from "
            "`web/styles/app.css` — and if no token says what this element needs, that is "
            "a design-system change to make in the stylesheet, not a ramp to reach for."
        )

    def test_the_scan_sees_the_tree(self) -> None:
        """A glob that silently found nothing would pass every case above by having none.

        The tree held fifty-nine templates when the ratchet closed; a floor of forty
        catches a broken path or a moved tree without tripping on ordinary deletions.
        """
        assert len(_templates()) >= 40


# `ink-faint` is gone. It ratcheted from seventeen uses to zero in tranche 2, the first
# name this file walked to zero and then pinned there.
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


# The compatibility aliases — `good`/`warn`/`bad`/`mute` for the status pairs, `brand` for
# verification, `navy` for ink — let migrated and unmigrated templates coexist while the
# ratchet fell. Tranche 9 retired the last template that spoke them and then the aliases
# themselves: a name that resolves is a name somebody will write, and two vocabularies for
# one palette is the drift §2.5 existed to end.

_ALIAS = re.compile(r"\b[\w-]+-(?:good|warn|bad|mute)-(?:ink|wash)\b|\b[\w-]+-(?:brand|navy)\b")


class TestTheLegacyAliasesStayGone:
    """Same shape as the `faint` guard, for the same reason: the old names survive in the
    prototype stylesheets under `docs/redesign/`, where they look authoritative."""

    def test_no_template_names_one(self) -> None:
        offenders = {
            name: sorted(set(found))
            for name in _templates()
            if (found := _ALIAS.findall((TEMPLATES_DIR / name).read_text(encoding="utf-8")))
        }
        assert not offenders, (
            f"These templates speak the retired alias dialect: {offenders}. The aliases "
            "were removed in tranche 9 — write the token itself: `success`/`warning`/"
            "`failure`/`muted` ink-and-wash pairs for good/warn/bad/mute, `verification` "
            "for brand, `ink` for navy."
        )

    def test_the_stylesheet_defines_none(self) -> None:
        """Both halves of the stylesheet: the source, so no alias can be reintroduced, and
        the compiled output, so a stale build carrying dead alias utilities fails here
        rather than shipping a vocabulary no template is allowed to use."""
        for sheet in (STYLES_DIR / "app.css", STATIC_DIR / "css" / "app.css"):
            found = sorted(set(_ALIAS.findall(sheet.read_text(encoding="utf-8"))))
            assert not found, f"{sheet.name} still defines retired aliases: {found}"


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

_LITERAL_INK = re.compile(r"bg-(" + "|".join(_FLIPPING_FILLS) + r")\b[^\"]*?\btext-(white|black)\b")


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
