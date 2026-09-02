"""Skill files are validated at authoring time, and errors name their line.

Task 35, §2.12. The examples here are written out as literal files rather than assembled,
because half of what is under test is *where* things sit: an error that says
``evidence_policy.min_sources`` is on line 12 is only testable against a file in which it
verifiably is.
"""

from __future__ import annotations

import pytest

from aer.core.schemas.skill import RESERVED_OUTPUT_FIELDS
from aer.skills.frontmatter import SkillFileError, parse_skill_file

# §2.12's own example, abridged. Line numbers matter: `scope` is line 8,
# `evidence_policy:` line 9, `min_sources` line 10, `output:` line 13.
MOAT_DURABILITY = """\
---
aer_skill: 1
key: moat_durability
kind: custom_section
title: "Competitive Moat Durability"
version: 3
required: false
scope: global
evidence_policy:
  min_sources: 3
  requires_primary: true
  max_tier: 4
output:
  summary: string
  durability_years: number
token_budget: 12000
allowed_tools: [search_facts, search_sources]
---

## What I want from this section

Assess how durable this company's competitive advantage is over my stated horizon.

## What I do not want

- Generic Porter's Five Forces recitation.
"""


class TestAValidFileParses:
    def test_the_frontmatter_is_structured(self) -> None:
        parsed = parse_skill_file(MOAT_DURABILITY)

        frontmatter = parsed.frontmatter
        assert frontmatter.key == "moat_durability"
        assert frontmatter.kind.value == "custom_section"
        assert frontmatter.evidence_policy is not None
        assert frontmatter.evidence_policy.min_sources == 3
        assert frontmatter.token_budget == 12000
        assert frontmatter.allowed_tools == ["search_facts", "search_sources"]

    def test_the_body_is_kept_verbatim_and_never_validated(self) -> None:
        parsed = parse_skill_file(MOAT_DURABILITY)

        assert parsed.body.startswith("## What I want from this section")
        assert "Porter's Five Forces" in parsed.body

    def test_the_hash_is_over_the_exact_source(self) -> None:
        # One changed byte is a new hash — in the *body*, which no validator reads, and
        # equally in the frontmatter. Hashing only one half would let the other drift
        # under a pinned version, which is what pinning and the import diff exist to
        # prevent.
        body_edit = MOAT_DURABILITY.replace("Porter's", "Porters")
        frontmatter_edit = MOAT_DURABILITY.replace("min_sources: 3", "min_sources: 4")

        original = parse_skill_file(MOAT_DURABILITY).content_hash
        assert parse_skill_file(body_edit).content_hash != original
        assert parse_skill_file(frontmatter_edit).content_hash != original
        assert parse_skill_file(MOAT_DURABILITY).content_hash == original


class TestErrorsNameTheirLine:
    def test_a_bad_scope_points_at_the_scope_line(self) -> None:
        broken = MOAT_DURABILITY.replace("scope: global", "scope: everywhere")

        with pytest.raises(SkillFileError) as caught:
            parse_skill_file(broken)

        [issue] = caught.value.issues
        assert issue.field == "scope"
        assert issue.line == 8
        assert "not a scope" in issue.message

    def test_a_bad_nested_field_points_at_its_own_line(self) -> None:
        broken = MOAT_DURABILITY.replace("  min_sources: 3", "  min_sources: -2")

        with pytest.raises(SkillFileError) as caught:
            parse_skill_file(broken)

        [issue] = caught.value.issues
        assert issue.field == "evidence_policy.min_sources"
        assert issue.line == 10

    def test_every_problem_is_reported_at_once(self) -> None:
        # One round trip, the same decision the settings loader took: an author should
        # not fix errors one save at a time.
        broken = MOAT_DURABILITY.replace("scope: global", "scope: everywhere").replace(
            "  min_sources: 3", "  min_sources: -2"
        )

        with pytest.raises(SkillFileError) as caught:
            parse_skill_file(broken)

        fields = {issue.field for issue in caught.value.issues}
        assert fields == {"scope", "evidence_policy.min_sources"}
        assert all(issue.line is not None for issue in caught.value.issues)

    def test_yaml_that_does_not_parse_names_the_line_it_broke_on(self) -> None:
        broken = MOAT_DURABILITY.replace('title: "Competitive Moat Durability"', "title: [unclosed")

        with pytest.raises(SkillFileError) as caught:
            parse_skill_file(broken)

        [issue] = caught.value.issues
        assert issue.line is not None
        assert "YAML" in issue.message

    def test_a_file_without_a_fence_is_refused_at_line_one(self) -> None:
        with pytest.raises(SkillFileError) as caught:
            parse_skill_file("# just a markdown file\n")

        [issue] = caught.value.issues
        assert issue.line == 1
        # The message matters as much as the line: a one-line file would satisfy
        # `line == 1` from the unclosed-fence path too, and that is the wrong diagnosis.
        assert "begins with" in issue.message

    def test_an_unclosed_fence_is_refused(self) -> None:
        with pytest.raises(SkillFileError) as caught:
            parse_skill_file("---\naer_skill: 1\nkey: x\n")

        [issue] = caught.value.issues
        assert "never closed" in issue.message

    def test_frontmatter_that_is_not_a_mapping_is_refused(self) -> None:
        with pytest.raises(SkillFileError) as caught:
            parse_skill_file("---\n- a\n- list\n---\nbody\n")

        [issue] = caught.value.issues
        assert "mapping" in issue.message


class TestTheReservedOutputFields:
    def test_the_reserved_set_itself_holds_the_report_owned_names(self) -> None:
        # The parametrised test below iterates the constant, so an emptied constant would
        # silently remove the tests that check it. This pin is what makes that loud.
        assert {"rating", "recommendation", "target_price", "valuation_range", "conviction"} <= (
            RESERVED_OUTPUT_FIELDS
        )
        # ADR 0080's six, reserved with the first sizing concept (ADR 0104).
        assert {
            "position_size",
            "weight",
            "recommended_weight",
            "action",
            "order_quantity",
            "stop_loss",
        } <= RESERVED_OUTPUT_FIELDS

    def test_a_size_is_refused_as_the_operators_decision(self) -> None:
        """ADR 0080: the reason is not ownership of a report figure, and the message says so."""
        broken = MOAT_DURABILITY.replace("  summary: string", "  recommended_weight: number")

        with pytest.raises(SkillFileError) as caught:
            parse_skill_file(broken)

        [issue] = caught.value.issues
        assert "operator's decision" in issue.message

    def test_a_conviction_is_refused_as_a_view_rather_than_as_an_owned_figure(self) -> None:
        """ADR 0074: the reason is not ownership, and the message says which reason it is."""
        broken = MOAT_DURABILITY.replace("  summary: string", "  conviction: number")

        with pytest.raises(SkillFileError) as caught:
            parse_skill_file(broken)

        [issue] = caught.value.issues
        assert "not a figure" in issue.message

    @pytest.mark.parametrize("reserved", sorted(RESERVED_OUTPUT_FIELDS))
    def test_an_output_contract_cannot_declare_one(self, reserved: str) -> None:
        """The writable path to a rating does not exist, starting here.

        There is deliberately no downstream check on what a custom section wrote into a
        rating field — because after this refusal there is no such field for anything to
        have been written into.
        """
        broken = MOAT_DURABILITY.replace("  summary: string", f"  {reserved}: string")

        with pytest.raises(SkillFileError) as caught:
            parse_skill_file(broken)

        [issue] = caught.value.issues
        assert issue.field == "output"
        assert "reserved" in issue.message

    def test_the_sections_own_confidence_is_not_reserved(self) -> None:
        # §2.12's example declares one deliberately: a section may say how sure it is of
        # itself. What it may not do is write the report's.
        allowed = MOAT_DURABILITY.replace("  summary: string", "  confidence: number")

        parsed = parse_skill_file(allowed)
        assert parsed.frontmatter.output is not None
        assert "confidence" in parsed.frontmatter.output


class TestKindAndShapeAgree:
    def test_a_methodology_skill_may_not_carry_an_output_contract(self) -> None:
        broken = MOAT_DURABILITY.replace("kind: custom_section", "kind: methodology")

        with pytest.raises(SkillFileError) as caught:
            parse_skill_file(broken)

        [issue] = caught.value.issues
        assert "produces no section" in issue.message

    def test_a_custom_section_without_a_budget_is_refused(self) -> None:
        broken = MOAT_DURABILITY.replace("token_budget: 12000\n", "")

        with pytest.raises(SkillFileError) as caught:
            parse_skill_file(broken)

        [issue] = caught.value.issues
        assert "token_budget" in issue.message

    def test_a_methodology_skill_of_prose_alone_is_valid(self) -> None:
        source = (
            "---\n"
            "aer_skill: 1\n"
            "key: owner_operator_lens\n"
            "kind: methodology\n"
            'title: "Weight owner-operator alignment"\n'
            "version: 1\n"
            "---\n"
            "I weight owner-operator alignment heavily. Say so where it bears on a thesis.\n"
        )

        parsed = parse_skill_file(source)
        assert parsed.frontmatter.kind.value == "methodology"
        assert parsed.frontmatter.output is None
