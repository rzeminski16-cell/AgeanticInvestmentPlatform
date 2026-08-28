"""Every component, rendered, and the two rules that keep the set worth having.

**A macro takes data and never classes.** A component that accepted a class string would let
a caller build a sheet that is not a sheet, and the reason to have components at all is that
there is one answer to what a sheet looks like. The rule is written in `_ui/index.html` and
enforced here, because a rule in a comment is a rule until somebody is in a hurry.

**Every macro is exported.** Jinja does not re-export a name brought in with `{% from %}`, so
the aggregator as first written exported nothing and `ui.card` raised `UndefinedError` —
invisibly, because it shipped before any page used it. That is now two assertions: the
aggregator names every macro that exists, and every name it exports actually renders.

The fixture is `tests/fixtures/components/components.html`, and it is **never a route**
(decision B6). A Components page in the navigation is a page that gets linked to and
eventually carries a state nobody rendered anywhere else — a gallery more complete than the
product. Here it is opened by this file and photographed by `tests/e2e/test_component_states.py`.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import pytest
from jinja2 import ChoiceLoader, Environment, FileSystemLoader, StrictUndefined

from aer.web.shell.provenance import (
    Confirmation,
    Provenance,
    ProvenanceRef,
    confirmed_by,
    suggested,
)
from aer.web.templating import TEMPLATES_DIR

FIXTURES = Path(__file__).parent / "fixtures" / "components"

# Every macro the aggregator must export. Written out rather than discovered, so a macro that
# quietly disappears is as loud as one that quietly appears.
EXPECTED: Final[frozenset[str]] = frozenset(
    {
        "provenance",
        "confirmation",
        "status",
        "grade",
        "callout",
        "button",
        "field",
        "disclosure",
        "page_header",
        "verdict",
        "sheet",
        "figure",
        "definition_list",
        "record_list",
        "record",
        "table",
        "cell",
        "evidence_spine",
        "decision_panel",
        "empty",
        "guide",
    }
)

# What a macro may never accept. `class` and `style` are the obvious two; `html` and `attrs`
# are the ways the same hole gets reopened by somebody who read the rule as being about the
# word rather than about the capability.
FORBIDDEN_ARGUMENTS: Final[tuple[str, ...]] = ("class", "classes", "style", "html", "attrs")

_MACRO = re.compile(r"\{%-?\s*macro\s+(\w+)\(([^)]*)\)")


def _component_files() -> list[Path]:
    return sorted((TEMPLATES_DIR / "_ui").glob("*.html"))


def _declared_macros() -> dict[str, list[str]]:
    """Every macro in `_ui/`, with its argument names. Private ones (`_mark`) are skipped."""
    found: dict[str, list[str]] = {}
    for path in _component_files():
        for name, arguments in _MACRO.findall(path.read_text(encoding="utf-8")):
            if name.startswith("_"):
                continue
            found[name] = [
                part.split("=")[0].strip() for part in arguments.split(",") if part.strip()
            ]
    return found


def _environment() -> Environment:
    return Environment(
        loader=ChoiceLoader([FileSystemLoader(FIXTURES), FileSystemLoader(TEMPLATES_DIR)]),
        undefined=StrictUndefined,
        autoescape=True,
    )


def _spine(state: str = "ok") -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = [
        {"kind": "Judged", "label": "The margin recovery is durable", "href": "/claims/1"},
        {
            "kind": "Calculated",
            "label": "Operating margin, 2026",
            "href": "/calculations/1",
            "grade": "Documented",
            "confirmation": "Confirmed by the operator on 24 August 2026",
        },
        {"kind": "Source fact", "label": "Annual report 2026, page 41", "href": "/sources/1"},
    ]
    if state == "missing":
        nodes[-1] = {
            "kind": "Source fact",
            "label": "Source unavailable",
            "href": "/sources/1",
            "state": "missing",
            "reason": "The artefact was purged under the retention policy on 1 August.",
        }
    if state == "incomplete":
        nodes[-1] = {
            "kind": "Source fact",
            "label": "Lineage incomplete",
            "href": "/sources/1",
            "state": "incomplete",
            "reason": "One input was recorded before lineage resolved by table.",
        }
    return nodes


def _context(theme: str = "") -> dict[str, Any]:
    when = datetime(2026, 8, 24, 9, 14, tzinfo=UTC)
    return {
        "theme": theme,
        "source_fact": ProvenanceRef(Provenance.SOURCE_FACT, "f1", "/sources/1"),
        "calculated": confirmed_by(
            Provenance.CALCULATED, "c1", "/calculations/1", name="the operator", at=when
        ),
        "attested": ProvenanceRef(
            Provenance.ATTESTED, "a1", "/attestations/1", confirmation=Confirmation.UNCONFIRMED
        ),
        "assumed": suggested(Provenance.ASSUMED, "s1", "/assumptions/1"),
        "judged": ProvenanceRef(Provenance.JUDGED, "j1", "/judgements/1"),
        "spine_ok": _spine(),
        "spine_missing": _spine("missing"),
        "spine_incomplete": _spine("incomplete"),
    }


def render_components(theme: str = "") -> str:
    """The fixture, rendered. Also used by the browser tests, which serve the result."""
    return _environment().get_template("components.html").render(**_context(theme))


class TestAMacroTakesDataAndNeverClasses:
    @pytest.mark.parametrize("name", sorted(_declared_macros()))
    def test_no_macro_accepts_a_presentation_argument(self, name: str) -> None:
        """The rule the whole set rests on.

        A caller who can pass a class can build a refusal in the success family, a sheet with
        no boundary, or a button that is 20px high — each of which looks like a component and
        is not one. Variants are a closed set mapped to complete literal classes instead.
        """
        offenders = sorted(
            argument
            for argument in _declared_macros()[name]
            if argument.lower() in FORBIDDEN_ARGUMENTS
        )
        assert not offenders, (
            f"`{name}` accepts {offenders}. A macro takes meaning, not presentation — add a "
            "variant to its closed set instead."
        )

    def test_the_rule_is_written_where_a_caller_will_read_it(self) -> None:
        aggregator = (TEMPLATES_DIR / "_ui" / "index.html").read_text(encoding="utf-8")

        assert "never classes" in aggregator


class TestTheAggregatorIsComplete:
    def test_every_declared_macro_is_exported(self) -> None:
        """A macro added to a component file and forgotten here is a page that fails when
        somebody first reaches for it, long after the commit that caused it."""
        aggregator = (TEMPLATES_DIR / "_ui" / "index.html").read_text(encoding="utf-8")
        exported = set(re.findall(r"\{%\s*set\s+(\w+)\s*=", aggregator))

        missing = sorted(set(_declared_macros()) - exported)
        assert not missing, f"declared in `_ui/` and not exported by the aggregator: {missing}"

    def test_the_expected_set_is_the_declared_set(self) -> None:
        """Both directions. A macro that disappears is as much a change as one that appears,
        and the list above is what a reader consults to know what exists."""
        assert set(_declared_macros()) == EXPECTED


class TestEveryComponentRenders:
    def test_the_fixture_renders_with_no_undefined(self) -> None:
        """`StrictUndefined`, so a macro reaching for a key its caller did not pass raises
        here rather than rendering an empty node on a page nobody tested."""
        assert "<main" in render_components()

    @pytest.mark.parametrize(
        "region",
        [
            "verdicts",
            "statuses",
            "callouts",
            "buttons",
            "fields",
            "sheets",
            "records",
            "provenance",
            "spines",
            "decisions",
            "guidance",
        ],
    )
    def test_every_region_of_the_fixture_is_present(self, region: str) -> None:
        assert f'id="{region}"' in render_components()

    def test_no_component_renders_an_unresolved_token(self) -> None:
        """A `bg-{{ tone }}-wash` that survived into the output would render with no colour
        at all, silently, and look like a styling bug."""
        rendered = render_components()

        assert "{{" not in rendered
        assert "{%" not in rendered


class TestWhatTheStatesActuallySay:
    def test_a_disabled_button_is_natively_disabled(self) -> None:
        """`aria-disabled` alone still submits. The native attribute is what stops it."""
        rendered = render_components()

        assert 'disabled aria-disabled="true"' in rendered

    def test_an_invalid_field_associates_its_error_rather_than_sitting_beside_it(self) -> None:
        """An error only visually beside its input tells a screen-reader user the form failed
        and not where."""
        rendered = render_components()

        assert 'aria-describedby="ceiling-hint ceiling-error"' in rendered
        assert 'aria-invalid="true"' in rendered
        assert 'id="ceiling-error"' in rendered

    def test_a_refusal_and_a_failure_announce_themselves(self) -> None:
        announced = render_components().count('role="alert"')

        assert announced >= 2, "a refusal and a failure must both be announced"

    def test_a_button_outside_its_form_names_it(self) -> None:
        """The decision panel places its buttons outside the form for layout. Without `form=`
        they submit nothing at all, and the page looks entirely correct."""
        assert 'form="gate-form"' in render_components()

    def test_the_integrity_promise_is_printed_where_a_decision_is_taken(self) -> None:
        """A gate binds a payload hash, and this is the plain-language form of that promise."""
        assert "deciding on exactly the evidence shown here" in render_components()

    def test_the_stale_panel_offers_no_way_to_submit(self) -> None:
        """The one state whose correctness is an *absence*. A form that still submitted after
        the evidence moved would break the sentence above it."""
        rendered = render_components()
        stale = rendered[rendered.index("NO DECISION AVAILABLE") :]

        assert "<button" not in stale

    def test_a_table_row_names_itself(self) -> None:
        """Without a row header a screen reader reads a column of bare figures."""
        assert 'scope="row"' in render_components()

    def test_a_disclosure_is_native(self) -> None:
        rendered = render_components()

        assert "<details" in rendered
        assert "<summary" in rendered
