"""How a disagreement's position reads on a page — gap A68.

Every disagreement on the live run's approval page carried "0 thesis (T1_REGULATORY)"
under both sides. A thesis conflict has no quantity: the two positions are arguments, not
numbers, so the ladder stores a placeholder zero it never compares. Rendered as though it
informed the reader, that zero is definitionally true of both sides and says nothing about
either — noise on the page doing the platform's most important work.

R12 fixed the appendix table with a conditional written twice in Jinja. A68 is the same
rule wanted in two more places, which is the point at which a rule living in a template
becomes four copies nobody owns. It lives in code now, and these pin it.
"""

from __future__ import annotations

from aer.core.disagreement import THESIS_UNIT, position_figure
from aer.web.templating import templates


class TestAThesisPositionShowsItsTierAlone:
    def test_the_placeholder_zero_never_reaches_a_reader(self) -> None:
        stored = {"value": "0", "unit": THESIS_UNIT, "tier": "T1_REGULATORY"}

        assert position_figure(stored) == "tier T1_REGULATORY"

    def test_the_two_sides_still_differ_where_they_genuinely_do(self) -> None:
        """Dropping the count must not flatten the sides into one another: the tier is the
        thing that actually differs between a draft thesis and its challenger."""
        draft = {"value": "0", "unit": THESIS_UNIT, "tier": "T1_REGULATORY"}
        challenge = {"value": "0", "unit": THESIS_UNIT, "tier": "T2_COMPANY"}

        assert position_figure(draft) != position_figure(challenge)


class TestAFactBackedPositionKeepsItsQuantity:
    def test_the_figure_and_its_tier_both_survive(self) -> None:
        stored = {"value": "1585000000", "unit": "USD", "tier": "T1_REGULATORY"}

        assert position_figure(stored) == "1585000000 USD (T1_REGULATORY)"

    def test_a_genuine_zero_is_not_mistaken_for_the_placeholder(self) -> None:
        """A filed figure that happens to be zero is a real answer, and the rule keys on
        the unit rather than on the value precisely so it survives."""
        stored = {"value": "0", "unit": "USD", "tier": "T1_REGULATORY"}

        assert position_figure(stored) == "0 USD (T1_REGULATORY)"


class TestTheRuleSurvivesAMalformedRecord:
    def test_a_missing_field_renders_rather_than_raising(self) -> None:
        """These records are JSONB written by earlier builds; a renderer that raised on an
        older shape would take the whole approval page down with it."""
        assert position_figure({}) == "()"

    def test_a_record_with_only_a_tier_still_says_the_tier(self) -> None:
        assert position_figure({"unit": THESIS_UNIT, "tier": "T3_EXCHANGE"}) == "tier T3_EXCHANGE"


class TestTheSurfacesShareIt:
    def test_the_filter_is_registered_for_the_templates(self) -> None:
        """Wired, not merely written: a template calling an unregistered filter fails at
        render time, on the page an operator is trying to approve from."""
        assert "position_figure" in templates.env.filters

    def test_the_registered_filter_is_the_rule_itself(self) -> None:
        rendered = templates.env.from_string("{{ p | position_figure }}").render(
            p={"value": "0", "unit": THESIS_UNIT, "tier": "T1_REGULATORY"}
        )

        assert rendered == "tier T1_REGULATORY"

    def test_no_surface_still_prints_the_placeholder_by_hand(self) -> None:
        """The reason the rule moved into code: four sites had to agree, and a conditional
        copied into Jinja is a rule nobody owns. If a template reaches for the raw fields
        again, this fails."""
        source = (templates.env.loader.get_source(templates.env, "runs/review.html"))[0]

        for field in ("position_a.value", "position_b.value"):
            assert field not in source, f"{field} is rendered by hand again"
