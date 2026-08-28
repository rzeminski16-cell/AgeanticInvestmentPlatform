"""What the scripts and the out-of-band swaps require the markup to keep calling things.

The interface overhaul rewrites every template in the research tool, and the four scripts in
`static/js/` reach into that markup by name. **Nothing today would notice a rename.** A
console whose `#run-spend` became `#spend` keeps rendering perfectly, keeps its server-side
value, and simply stops updating — the failure is a number that is correct on load and
quietly wrong four minutes later, which is the worst shape a defect can take on a page whose
whole job is to say what is happening now.

The same is true of the out-of-band swaps. htmx targets an id; an id that moved means a
response that lands nowhere, silently, because a swap with no target is not an error.

So this is the inventory as an assertion. It is deliberately a *static* check over the
templates and the scripts rather than a rendered one: it costs no database and no browser, so
it runs in the default suite on every change, which is where a rename actually happens.

**Uniqueness is the rendered half and lives elsewhere.** That an id appears *once per page* is
`tests/e2e/test_shell.py`'s to prove, because a template can only be counted once it has been
composed with everything that includes it.
"""

from __future__ import annotations

import re
from typing import Final

import pytest

from aer.web.shell.badges import registered_badges
from aer.web.templating import STATIC_DIR, TEMPLATES_DIR

SCRIPTS: Final = STATIC_DIR / "js"

# Ids a script reaches for by name. Grouped by the file that depends on each, so a failure
# says which behaviour breaks rather than only which string is missing.
#
# Read from the scripts by `_ids_named_by`, and asserted against this list as well — a script
# that grew a new dependency without it being recorded here is a dependency nobody reviewed.
REQUIRED_IDS: Final[dict[str, tuple[str, ...]]] = {
    "drawer.js": ("aer-drawer", "aer-drawer-body", "aer-drawer-title"),
    "console.js": ("run-console", "run-spend", "run-status", "stream-note"),
}

# Attributes the scripts key on instead of ids, because the thing they address repeats.
#
# `data-field` and `data-step` are how `console.js` finds the row for a step it has been told
# about without composing a selector out of user data; `data-filters` and `data-search` are
# how `tables.js` finds a table and its rows. Each is a contract in exactly the way an id is.
REQUIRED_ATTRIBUTES: Final[dict[str, tuple[str, ...]]] = {
    "console.js": ("data-step", "data-field", "data-started-at", "data-job-id"),
    "tables.js": ("data-filters", "data-search"),
    "drawer.js": ("data-drawer-title",),
}

# Ids an htmx out-of-band swap targets. A response naming one of these lands nowhere if the
# page has stopped rendering it, and htmx does not complain.
#
# `aer-badge-{key}` is a pattern rather than a literal: the nav renders one slot per badge
# key and `_shell/badges.html` answers with the same id. `_badge_ids` resolves it against the
# registry so the two cannot drift apart.
OOB_TARGETS: Final[tuple[str, ...]] = ("csrf-input",)

_ID_IN_SCRIPT = re.compile(
    r"""getElementById\(\s*["']([\w-]+)|querySelector(?:All)?\(\s*["']#([\w-]+)"""
)
_ID_IN_TEMPLATE = re.compile(r"""\bid=["']([^"'{}]+)["']""")


def _templates() -> list[str]:
    return sorted(p.relative_to(TEMPLATES_DIR).as_posix() for p in TEMPLATES_DIR.rglob("*.html"))


def _all_template_text() -> str:
    return "\n".join((TEMPLATES_DIR / name).read_text(encoding="utf-8") for name in _templates())


def _declared_ids() -> set[str]:
    """Every literal id the templates declare, ignoring the ones built from a variable."""
    return set(_ID_IN_TEMPLATE.findall(_all_template_text()))


def _ids_named_by(script: str) -> set[str]:
    found = _ID_IN_SCRIPT.findall((SCRIPTS / script).read_text(encoding="utf-8"))
    return {name or hashed for name, hashed in found}


def _badge_ids() -> set[str]:
    """The slot ids the nav renders and the fragment answers with, from the registry."""
    return {f"aer-badge-{provider.key}" for provider in registered_badges()}


class TestEveryIdAScriptNamesExists:
    @pytest.mark.parametrize(
        ("script", "element_id"),
        [(script, element_id) for script, ids in REQUIRED_IDS.items() for element_id in ids],
    )
    def test_the_template_still_declares_it(self, script: str, element_id: str) -> None:
        assert element_id in _declared_ids(), (
            f"`static/js/{script}` reaches for #{element_id} and no template declares it. "
            "The script will find nothing and fail silently — no error, no missing element, "
            "just a page that stops updating. Either restore the id or change the script in "
            "the same commit."
        )

    @pytest.mark.parametrize("script", sorted(REQUIRED_IDS))
    def test_no_script_grew_an_unrecorded_dependency(self, script: str) -> None:
        """A new id a script depends on is a new contract, and belongs in the list above."""
        unrecorded = sorted(_ids_named_by(script) - set(REQUIRED_IDS[script]))
        assert not unrecorded, (
            f"`static/js/{script}` now reaches for {unrecorded}, which REQUIRED_IDS does not "
            "record. Add them — an undeclared dependency is one no reviewer of a template "
            "rename would know to look for."
        )


class TestEveryAttributeAScriptKeysOnExists:
    @pytest.mark.parametrize(
        ("script", "attribute"),
        [(s, a) for s, attrs in REQUIRED_ATTRIBUTES.items() for a in attrs],
    )
    def test_the_template_still_uses_it(self, script: str, attribute: str) -> None:
        assert attribute in _all_template_text(), (
            f"`static/js/{script}` keys on [{attribute}] and no template carries it. Unlike "
            "a missing id this does not even leave an empty element behind — the script's "
            "query returns nothing and the behaviour is simply absent."
        )


class TestTheOutOfBandTargetsSurvive:
    """An htmx response that names a missing id lands nowhere, and nothing says so."""

    @pytest.mark.parametrize("element_id", OOB_TARGETS)
    def test_a_named_target_is_still_declared(self, element_id: str) -> None:
        assert element_id in _declared_ids(), (
            f"An out-of-band swap targets #{element_id} and no template declares it. The "
            "response will be discarded silently. For #csrf-input that means a form whose "
            "token is never refreshed, so the operator's next submission is refused with an "
            "expired token and no explanation."
        )

    def test_the_badge_slot_matches_the_registry(self) -> None:
        """The nav's slot and the fragment's response are one id, derived from one registry.

        Two places compose `aer-badge-{key}` — `_nav.html` from `item.badge_key` and
        `_shell/badges.html` from `badge.key` — and they meet only at run time. What holds
        them together is that both read the same registry, so this asserts the registry has
        something to give them rather than that either template spelt it correctly.
        """
        assert _badge_ids(), "no badge provider is registered, so the nav renders no slot"
        for template in ("_nav.html", "_shell/badges.html"):
            text = (TEMPLATES_DIR / template).read_text(encoding="utf-8")
            assert "aer-badge-" in text, (
                f"{template} no longer composes a badge slot id. The nav ships an empty slot "
                "and the fragment fills it out of band; if either stops using the prefix, "
                "the count silently never arrives."
            )
