"""Provenance badges: a link or nothing, and two chips rather than one.

The rules under test are ADR 0073's, and each of them forbids a specific thing somebody
will otherwise do: render a badge that leads nowhere, say "Confirmed" without saying by
whom, or type a label into a page instead of asking the macro for one.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

import pytest

from aer.web.shell.provenance import (
    Confirmation,
    Provenance,
    ProvenanceRef,
    confirmed_by,
    suggested,
)
from aer.web.templating import TEMPLATES_DIR, templates

MACRO_FILE = TEMPLATES_DIR / "_ui" / "provenance.html"


class TestABadgeIsALink:
    def test_a_ref_without_an_href_is_refused(self) -> None:
        # Not defaulted, not optional. A badge reading "Calculated" that leads nowhere
        # asserts a lineage while refusing to show it.
        with pytest.raises(ValueError, match="no href"):
            ProvenanceRef(kind=Provenance.CALCULATED, identifier="calc-1", href="")

    def test_the_rendered_badge_carries_the_href(self) -> None:
        ref = ProvenanceRef(
            kind=Provenance.CALCULATED, identifier="calc-1", href="/calculations/calc-1"
        )

        markup = _render("provenance", ref)

        assert 'href="/calculations/calc-1"' in markup
        assert "Calculated" in markup


class TestConfirmationIsItsOwnAxis:
    def test_a_calculation_nobody_confirmed_is_representable(self) -> None:
        """The state one chip could not express, and the ordinary state of an assumptions
        page. Where it came from and whether anybody agreed are different questions."""
        ref = ProvenanceRef(kind=Provenance.CALCULATED, identifier="c", href="/calculations/c")

        assert ref.kind is Provenance.CALCULATED
        assert ref.confirmation is Confirmation.UNCONFIRMED

    def test_confirmed_must_name_somebody(self) -> None:
        with pytest.raises(ValueError, match="must name who"):
            ProvenanceRef(
                kind=Provenance.ASSUMED,
                identifier="a",
                href="/requests/1/assumptions/a",
                confirmation=Confirmation.CONFIRMED,
            )

    def test_confirmed_reads_as_who_and_when(self) -> None:
        ref = confirmed_by(
            Provenance.ASSUMED,
            "a",
            "/requests/1/assumptions/a",
            name="Jane Analyst",
            at=datetime(2026, 3, 12, tzinfo=UTC),
        )

        assert ref.confirmation_text == "Confirmed by Jane Analyst on 12 March 2026"

    def test_confirmed_without_a_date_still_names_the_person(self) -> None:
        ref = confirmed_by(Provenance.ASSUMED, "a", "/x", name="Jane Analyst")

        assert ref.confirmation_text == "Confirmed by Jane Analyst"

    def test_suggested_is_not_unconfirmed(self) -> None:
        # Different states: one was proposed and awaits a decision, the other was recorded
        # and nobody has looked at it.
        assert suggested(Provenance.ASSUMED, "a", "/x").confirmation is Confirmation.SUGGESTED
        assert (
            ProvenanceRef(kind=Provenance.ASSUMED, identifier="a", href="/x").confirmation
            is Confirmation.UNCONFIRMED
        )


class TestTheLabelsLiveInOnePlace:
    """ADR 0073's grep, in the shape ADR 0013 uses for section keys.

    The required `href` stops a badge being built without a destination. This stops one
    being typed around the macro entirely, which is the other way the vocabulary drifts:
    two spellings of "Source fact" is two things a reader has to learn are the same.
    """

    def test_no_template_spells_a_provenance_label_outside_the_macro(self) -> None:
        labels = [member.value for member in Provenance]
        offenders: list[str] = []

        for template in TEMPLATES_DIR.rglob("*.html"):
            if template == MACRO_FILE:
                continue
            body = template.read_text(encoding="utf-8")
            offenders.extend(
                f"{template.relative_to(TEMPLATES_DIR)}: {label}"
                for label in labels
                if re.search(rf">\s*{re.escape(label)}\s*<", body)
            )

        assert not offenders, (
            f"provenance labels typed outside _ui/provenance.html: {offenders}. Import the "
            "macro and pass it a ProvenanceRef."
        )

    def test_the_macro_file_actually_contains_them(self) -> None:
        # Otherwise the test above passes by asserting nothing, which is the failure mode of
        # every grep-shaped test.
        body = MACRO_FILE.read_text(encoding="utf-8")

        for member in Provenance:
            assert member.value in body


def _render(macro: str, ref: ProvenanceRef) -> str:
    template = templates.env.get_template("_ui/provenance.html")
    return str(getattr(template.module, macro)(ref))
