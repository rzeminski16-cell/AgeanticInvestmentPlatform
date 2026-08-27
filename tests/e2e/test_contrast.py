"""Every sanctioned pairing, measured from the colour the browser actually resolved.

The design system ships a table of ratios. This does not trust it. **"It uses a token" is not
a contrast test** — a token is a name, and a name can be wired to the wrong value, redefined
by a later block, or resolved differently under a media query than under an attribute. Each of
those renders a page that looks deliberate and fails AA.

So the values come back through `getComputedStyle`, after the cascade, the media query and the
theme attribute have all had their say, as `rgb(...)`. Python does the arithmetic — the one
rule, applied to a stylesheet: deterministic code owns every number.

**Three scopes, because the palette has three.** Light and dark are the two the operator
chooses between. The third is the navigation rail, which is `#102b35` on a light page and on a
dark one, and whose focus ring was landing at 2.04:1 before ADR 0088 — a WCAG 2.2 SC 1.4.11
failure that no table covered, because the rail's colours were in no table.

A pairing that is not here is a pairing nobody has measured. Adding a colour combination to a
template means adding a row.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

import pytest

if TYPE_CHECKING:
    from playwright.sync_api import Page

# WCAG 2.2: normal text needs 4.5:1; large text and meaningful non-text graphics need 3:1.
TEXT: Final = 4.5
GRAPHIC: Final = 3.0

# Every background a reader meets text on. `surface-raised` is the drawer and the menu;
# the five washes are the semantic components.
BACKGROUNDS: Final[tuple[str, ...]] = (
    "canvas",
    "surface",
    "surface-raised",
    "surface-sunken",
    "verification-wash",
    "decision-wash",
    "success-wash",
    "refusal-wash",
    "failure-wash",
)

# Foundation text on every one of them. `ink-subtle` is the quietest text the system permits
# and is the row that matters: it is where a fourth, fainter grey would have gone (D3).
_FOUNDATION = [(ink, bg, TEXT) for ink in ("ink", "ink-muted", "ink-subtle") for bg in BACKGROUNDS]

# A semantic ink on its own wash, and on the two neutral sheets it also appears on.
_SEMANTIC = [
    (f"{family}-ink", bg, TEXT)
    for family in ("success", "warning", "refusal", "failure", "info", "muted")
    for bg in (f"{family}-wash", "surface", "canvas")
]

# Text on a filled action, at rest and under the pointer. A hover state that drops below AA is
# a control that fails only while somebody is using it.
_FILLED = [
    ("on-verification", "verification", TEXT),
    ("on-verification", "verification-strong", TEXT),
    ("on-decision", "decision", TEXT),
    ("on-decision", "decision-strong", TEXT),
    ("on-danger-action", "danger-action", TEXT),
    ("on-danger-action", "danger-action-hover", TEXT),
]

_LINKS = [
    (ink, bg, TEXT)
    for ink in ("verification", "verification-strong")
    for bg in ("surface", "canvas", "verification-wash")
]

# Non-text, at 3:1. `control-boundary` exists because `line` and `line-strong` are deliberately
# below it: they are ledger rules and may never outline a control, a selected region, an
# evidence node or a chart mark.
_NON_TEXT = [
    (ink, bg, GRAPHIC)
    for ink in ("control-boundary", "focus-ring")
    for bg in ("surface", "canvas", "surface-sunken", "surface-raised")
] + [("verification", "surface", GRAPHIC), ("verification", "canvas", GRAPHIC)]

PAIRINGS: Final[tuple[tuple[str, str, float], ...]] = tuple(
    _FOUNDATION + _SEMANTIC + _FILLED + _LINKS + _NON_TEXT
)

# The rail keeps the dark scheme's colours whatever the page is doing, so it is measured on its
# own rather than assumed to inherit either. `nav-selected` is deliberately absent: at 1.21:1
# it is decorative, and ADR 0088 records that selection is carried by the accent rule and by
# `aria-current`, never by the fill.
RAIL_PAIRINGS: Final[tuple[tuple[str, str, float], ...]] = (
    ("nav-ink", "nav-surface", TEXT),
    ("nav-muted", "nav-surface", TEXT),
    ("nav-accent", "nav-surface", GRAPHIC),
    # And the generic accents, which a shared macro drops onto the rail without knowing it is
    # there. These are the ones that were failing: the light focus ring measured 2.04:1.
    ("focus-ring", "nav-surface", GRAPHIC),
    ("verification", "nav-surface", GRAPHIC),
    ("decision", "nav-surface", GRAPHIC),
    ("ink", "nav-surface", TEXT),
)

# One probe per pairing, read in one round trip. Each element resolves both custom properties
# through the full cascade; `getComputedStyle` then reports them as `rgb(...)`.
_PROBE = """
([pairings, scheme]) => {
  const host = document.createElement('div');
  if (scheme) host.setAttribute('data-scheme', scheme);
  document.body.appendChild(host);
  const measured = pairings.map(([ink, bg]) => {
    const el = document.createElement('span');
    el.style.color = `var(--aer-${ink})`;
    el.style.backgroundColor = `var(--aer-${bg})`;
    host.appendChild(el);
    const style = getComputedStyle(el);
    return [style.color, style.backgroundColor];
  });
  host.remove();
  return measured;
}
"""


def _channel(value: float) -> float:
    value /= 255
    return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4


def _luminance(rgb: str) -> float:
    parts = [float(n) for n in rgb.removeprefix("rgb(").rstrip(")").replace(",", " ").split()[:3]]
    red, green, blue = (_channel(part) for part in parts)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def _ratio(foreground: str, background: str) -> float:
    first, second = _luminance(foreground), _luminance(background)
    lighter, darker = max(first, second), min(first, second)
    return (lighter + 0.05) / (darker + 0.05)


def _measure(
    page: Page,
    live_server: str,
    pairings: tuple[tuple[str, str, float], ...],
    *,
    theme: str,
    scheme: str = "",
) -> list[tuple[str, str, float, float]]:
    page.goto(f"{live_server}/overview")
    page.evaluate(f"document.documentElement.setAttribute('data-theme', {theme!r})")
    measured = page.evaluate(_PROBE, [[[ink, bg] for ink, bg, _ in pairings], scheme])
    return [
        (ink, bg, floor, _ratio(colour, background))
        for (ink, bg, floor), (colour, background) in zip(pairings, measured, strict=True)
    ]


def _report(results: list[tuple[str, str, float, float]]) -> None:
    failures = [
        f"{ink} on {bg}: {ratio:.2f}:1, needs {floor}:1"
        for ink, bg, floor, ratio in results
        if ratio < floor
    ]
    assert not failures, "these pairings do not meet WCAG 2.2 AA:\n  " + "\n  ".join(failures)


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_every_sanctioned_pairing_meets_aa(page: Page, live_server: str, theme: str) -> None:
    _report(_measure(page, live_server, PAIRINGS, theme=theme))


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_the_navigation_rail_meets_aa_whatever_the_page_is_doing(
    page: Page, live_server: str, theme: str
) -> None:
    """The failure ADR 0088 exists for, watched in both themes.

    On a light page the rail is a dark region, so the *light* accents are the ones that would
    be painted on it without the scope — and every one of them fails 3:1 there.
    """
    _report(_measure(page, live_server, RAIL_PAIRINGS, theme=theme, scheme="dark"))


def test_a_decorative_rule_is_not_quietly_strong_enough_to_be_used(
    page: Page, live_server: str
) -> None:
    """The complement, and the one assertion here that wants a *low* number.

    `line` and `line-strong` are below 3:1 on purpose. If somebody strengthens them to pass,
    the next person will reasonably outline an input with one — and `control-boundary`, which
    is the token for that and is measured above, quietly stops being used.
    """
    measured = _measure(
        page,
        live_server,
        (("line", "surface", 0.0), ("line-strong", "surface", 0.0)),
        theme="light",
    )

    for ink, bg, _floor, ratio in measured:
        assert ratio < GRAPHIC, (
            f"{ink} on {bg} is now {ratio:.2f}:1. A ledger rule that clears 3:1 will be used "
            "to outline a control, and `control-boundary` is the token for that."
        )
