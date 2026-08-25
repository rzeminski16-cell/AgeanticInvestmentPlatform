"""The accessibility harness: axe-core, vendored, injected from disk.

WCAG 2.2 AA is the floor for the interface overhaul (`docs/plan/interface-overhaul-testing.md`),
and four separate checks in the design handoff ask for axe-core over every page family in both
schemes. Nothing in the suite could run one: the default suite makes no network request by
design, and `package.json` held Tailwind and htmx and nothing else.

So axe is vendored, exactly as `htmx.min.js` is and for the same reason — a test that reached a
CDN would be a test that fails on a train, and a suite that needs the internet is a suite whose
red builds get ignored.

**It is a test asset and never a served one.** `TestTheHarnessIsNotShipped` asserts it is
outside the static tree: a page that loaded a 568 kB testing library would be paying for the
suite's convenience in every operator's first paint.

**What it is worth.** Automated checking finds something like half of what is wrong with a
page — enough to be worth a red build, nowhere near enough to be the whole answer. The other
half is `docs/developers/testing-by-hand.md` §8.3: the keyboard alone, a narrow window,
contrast in both schemes, and 200% zoom.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from playwright.sync_api import Page

__all__ = ["AXE_SOURCE", "PINNED_SHA256", "Violation", "audit", "describe"]

AXE_SOURCE: Final = Path(__file__).parent / "fixtures" / "axe" / "axe.min.js"

# axe-core 4.13.0, Mozilla Public License 2.0 (`LICENSE` beside the file).
#
# Pinned in the shape `tests/test_fonts.py` settled for the typeface: a vendored asset's
# version and hash are recorded where a red build can find them, because a commit message is
# a record nobody diffs and a swapped minified file reviews as "+1 -1".
PINNED_SHA256: Final = "c24f097bd2f451d4f933e8bc7d8d539f8672a2ebcb5cc9f9f3eec8ca9470a0c1"

# What a failure has to be before it fails a build.
#
# Starting at the two most serious tiers rather than at everything, deliberately. A gate that
# goes red on its first run for two hundred `moderate` findings is a gate somebody switches
# off; one that goes red for a missing form label is one somebody fixes. `moderate` joins the
# list once the baseline is clean — that promotion is tranche 9's, and it is in the plan.
BLOCKING_IMPACTS: Final = frozenset({"critical", "serious"})

# Rules axe cannot judge from one page, rather than rules we disagree with.
#
# `region` wants every piece of content inside a landmark, which is a whole-page judgement it
# makes wrongly about a fragment rendered on its own — and several of the surfaces here are
# fragments by design (the drawer's body, the badge response). Nothing else is disabled: a
# rule this platform genuinely wanted to argue with would need a reason written here, and so
# far none does.
FRAGMENT_EXEMPT_RULES: Final = frozenset({"region"})


class Violation(dict[str, Any]):
    """One axe finding, kept as its raw dictionary so a failure can print all of it."""

    @property
    def rule(self) -> str:
        return str(self.get("id", "?"))

    @property
    def impact(self) -> str:
        return str(self.get("impact") or "unknown")

    @property
    def targets(self) -> list[str]:
        """The CSS selectors axe matched, which is what makes a finding actionable."""
        found: list[str] = []
        for node in self.get("nodes", []):
            found.extend(str(target) for target in node.get("target", []))
        return found


def audit(page: Page, *, is_fragment: bool = False) -> list[Violation]:
    """Run axe against the page as it currently stands, and return what blocks.

    Injected from the vendored file rather than fetched, and re-injected per call: a page that
    navigated has a fresh document, and axe lives in the document rather than in the context.

    Args:
        page: A Playwright page, already at the state under test — the right theme chosen, the
            right viewport set, any disclosure already open. **axe sees what is rendered**, so
            a control still behind a closed `<details>` is a control it will not check.
        is_fragment: True when the markup under test is a fragment rather than a whole page,
            which exempts the landmark rule. See `FRAGMENT_EXEMPT_RULES`.

    Returns:
        Only the findings at a blocking impact. An empty list is a pass.
    """
    page.add_script_tag(path=str(AXE_SOURCE))
    disabled = sorted(FRAGMENT_EXEMPT_RULES) if is_fragment else []
    # `runOnly` is deliberately not set: the whole rule set runs and the *impact* decides what
    # blocks, so a rule promoted upstream from moderate to serious starts failing us without
    # anybody having to notice the release note.
    raw = page.evaluate(
        """async (disabled) => {
            const options = disabled.length
                ? { rules: Object.fromEntries(disabled.map((id) => [id, { enabled: false }])) }
                : {};
            const result = await window.axe.run(document, options);
            return result.violations;
        }""",
        disabled,
    )
    return [Violation(item) for item in raw if item.get("impact") in BLOCKING_IMPACTS]


def describe(violations: list[Violation]) -> str:
    """A failure message somebody can act on without opening a browser.

    The rule, what it means, and the selectors it matched. axe's own `help` text is better
    than anything paraphrasing it would be, so it is quoted rather than summarised.
    """
    if not violations:
        return "no blocking accessibility violations"
    lines = [f"{len(violations)} blocking accessibility violation(s):"]
    for found in sorted(violations, key=lambda v: (v.impact, v.rule)):
        lines.append(f"  [{found.impact}] {found.rule} — {found.get('help', '')}")
        for target in found.targets[:5]:
            lines.append(f"      at {target}")
        if len(found.targets) > 5:
            lines.append(f"      … and {len(found.targets) - 5} more")
        if help_url := found.get("helpUrl"):
            lines.append(f"      {help_url}")
    return "\n".join(lines)


def digest(path: Path = AXE_SOURCE) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
