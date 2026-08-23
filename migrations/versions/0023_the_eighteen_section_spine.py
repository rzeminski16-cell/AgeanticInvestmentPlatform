"""Seed the eighteen-section spine: sixteen new built-in section definitions.

Task 44. `docs/archive/PLAN.md` commits to eighteen built-in sections ("the 18-section
institutional spine") without enumerating them; the enumeration is decided in
`docs/archive/phase-5-plan.md` and seeded here. Two of the eighteen were seeded by migration 0006
(`executive_summary`, `historical_financial_analysis`); this migration adds the remaining
sixteen. Rows, not code: the workflow, the renderer and the exporters iterate these
tables, so the whole spine arrives with no code change beyond what fills it.

Two of the new sections are **deterministic** — filled by code from the run's own recorded
state, spending no tokens — and declare `token_budget = 0` to say so honestly. The 0006
check constraint required a positive budget, which was true of every section that existed
then; it relaxes here to non-negative, still refusing the meaningless negative budget.

Every model-written contract carries at least one array-of-objects property whose items
may name `calculation_id` / `source_document_id`: those keys are how content cites, how
the renderer footnotes, and how a section meets its evidence floor. The 0006
`executive_summary` contract had no such field — a summary structurally unable to cite a
headline figure — so this migration publishes its version 2 with one appended. A new
version row, never an edit: definitions are immutable per version, the registry takes the
highest, and a report already rendered keeps the version it pinned.

Revision ID: 0023
Revises: 0022
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


# The evidence floor most model-written sections inherit, mirroring migration 0006. A
# skill-authored section may tighten these and may never loosen them.
_EVIDENCE_POLICY = {
    "min_sources": 1,
    "max_tier": "T4_LICENSED_MARKET",
    "requires_primary": True,
    "allow_forward_looking": False,
}

# Sections whose subject is inherently prospective — a thesis, an outlook, a catalyst —
# may carry FORWARD_LOOKING claims; the claim rules still require a stated basis.
_FORWARD_LOOKING_POLICY = {**_EVIDENCE_POLICY, "allow_forward_looking": True}

# The deterministic sections record what the platform itself did. Their evidence is the
# run's own rows, so an external-source floor would be a floor nothing could meet.
_DETERMINISTIC_POLICY = {
    "min_sources": 0,
    "max_tier": "T4_LICENSED_MARKET",
    "requires_primary": False,
    "allow_forward_looking": False,
}


def _figure_items(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """The shared shape of a cited figure row: label, value, unit, and what it cites."""
    properties: dict[str, Any] = {
        "label": {"type": "string"},
        "value": {"type": "string"},
        "unit": {"type": "string"},
        "calculation_id": {"type": "string"},
        "source_document_id": {"type": "string"},
    }
    if extra:
        properties = {**extra, **properties}
    return {
        "type": "object",
        "required": ["label", "value", "unit"],
        "properties": properties,
    }


def _commentary(description: str) -> dict[str, Any]:
    return {"type": "string", "title": "Commentary", "description": description}


# Version 2 of the 0006 contract: the same three fields in the same order, plus a
# citation-carrying figures table the original lacked.
_EXECUTIVE_SUMMARY_V2: dict[str, Any] = {
    "type": "object",
    "title": "Executive Summary",
    "required": ["thesis", "key_points"],
    "properties": {
        "thesis": {
            "type": "string",
            "title": "Thesis",
            "description": "The central view, in two or three sentences.",
        },
        "key_points": {
            "type": "array",
            "title": "Key Points",
            "items": {"type": "string"},
            "minItems": 1,
        },
        "key_risks": {
            "type": "array",
            "title": "Key Risks",
            "items": {"type": "string"},
        },
        "headline_figures": {
            "type": "array",
            "title": "Headline Figures",
            "items": _figure_items(),
        },
    },
}


_CONTRACTS: dict[str, dict[str, Any]] = {
    "investment_thesis": {
        "type": "object",
        "title": "Investment Thesis",
        "required": ["thesis_statement", "supporting_pillars"],
        "properties": {
            "thesis_statement": {
                "type": "string",
                "title": "Thesis Statement",
                "description": "The central view and why it is held, in three or four sentences.",
            },
            "supporting_pillars": {
                "type": "array",
                "title": "Supporting Pillars",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "required": ["pillar", "evidence"],
                    "properties": {
                        "pillar": {"type": "string"},
                        "evidence": {"type": "string"},
                        "calculation_id": {"type": "string"},
                        "source_document_id": {"type": "string"},
                    },
                },
            },
            "what_would_change_the_view": {
                "type": "array",
                "title": "What Would Change The View",
                "items": {"type": "string"},
            },
        },
    },
    "business_overview": {
        "type": "object",
        "title": "Business Overview",
        "required": ["commentary"],
        "properties": {
            "commentary": _commentary("What the company sells, to whom, and how it is paid."),
            "revenue_streams": {
                "type": "array",
                "title": "Revenue Streams",
                "items": _figure_items(),
            },
            "operating_footprint": {
                "type": "array",
                "title": "Operating Footprint",
                "items": {"type": "string"},
            },
        },
    },
    "segment_analysis": {
        "type": "object",
        "title": "Segment Analysis",
        "required": ["commentary"],
        "properties": {
            "commentary": _commentary(
                "How the reported segments differ in growth, margin and capital intensity."
            ),
            "segments": {"type": "array", "title": "Segments", "items": _figure_items()},
        },
    },
    "industry_landscape": {
        "type": "object",
        "title": "Industry & Competitive Positioning",
        "required": ["commentary", "competitive_position"],
        "properties": {
            "commentary": _commentary("The structure of the industry the company competes in."),
            "competitive_position": {
                "type": "string",
                "title": "Competitive Position",
                "description": "Where this company sits in that structure, and why it holds.",
            },
            "industry_trends": {
                "type": "array",
                "title": "Industry Trends",
                "items": {"type": "string"},
            },
            "market_shares": {
                "type": "array",
                "title": "Market Shares",
                "items": _figure_items(),
            },
        },
    },
    "management_governance": {
        "type": "object",
        "title": "Management & Governance",
        "required": ["commentary"],
        "properties": {
            "commentary": _commentary(
                "Who runs the company, their record, and how they are incentivised."
            ),
            "governance_observations": {
                "type": "array",
                "title": "Governance Observations",
                "items": {"type": "string"},
            },
            "compensation_figures": {
                "type": "array",
                "title": "Compensation Figures",
                "items": _figure_items(),
            },
        },
    },
    "earnings_quality": {
        "type": "object",
        "title": "Earnings Quality",
        "required": ["commentary"],
        "properties": {
            "commentary": _commentary("How faithfully reported earnings reflect cash economics."),
            "red_flags": {"type": "array", "title": "Red Flags", "items": {"type": "string"}},
            "figures": {"type": "array", "title": "Figures", "items": _figure_items()},
        },
    },
    "balance_sheet_liquidity": {
        "type": "object",
        "title": "Balance Sheet & Liquidity",
        "required": ["commentary", "figures"],
        "properties": {
            "commentary": _commentary("Leverage, maturity structure and the liquidity runway."),
            "figures": {"type": "array", "title": "Figures", "items": _figure_items()},
            "observations": {
                "type": "array",
                "title": "Observations",
                "items": {"type": "string"},
            },
        },
    },
    "cash_flow_analysis": {
        "type": "object",
        "title": "Cash Flow Analysis",
        "required": ["commentary", "figures"],
        "properties": {
            "commentary": _commentary("Where cash is generated, where it goes, and how durably."),
            "figures": {"type": "array", "title": "Figures", "items": _figure_items()},
        },
    },
    "capital_allocation": {
        "type": "object",
        "title": "Capital Allocation",
        "required": ["commentary"],
        "properties": {
            "commentary": _commentary(
                "The record of what management does with the cash it controls."
            ),
            "uses_of_capital": {
                "type": "array",
                "title": "Uses of Capital",
                "items": {"type": "string"},
            },
            "figures": {"type": "array", "title": "Figures", "items": _figure_items()},
        },
    },
    "growth_outlook": {
        "type": "object",
        "title": "Growth Outlook",
        "required": ["commentary"],
        "properties": {
            "commentary": _commentary("The drivers of growth from here, and their durability."),
            "growth_drivers": {
                "type": "array",
                "title": "Growth Drivers",
                "items": {"type": "string"},
            },
            "figures": {"type": "array", "title": "Figures", "items": _figure_items()},
        },
    },
    "valuation_dcf": {
        "type": "object",
        "title": "Valuation — Discounted Cash Flow",
        "required": ["commentary"],
        "properties": {
            "commentary": _commentary(
                "What the discounted cash flow concludes and what it turns on."
            ),
            "key_assumptions": {
                "type": "array",
                "title": "Key Assumptions",
                "items": {"type": "string"},
            },
            "figures": {
                "type": "array",
                "title": "Valuation Figures",
                "items": _figure_items(),
            },
        },
    },
    "scenarios_sensitivities": {
        "type": "object",
        "title": "Scenarios & Sensitivities",
        "required": ["commentary"],
        "properties": {
            "commentary": _commentary("The bear, base and bull cases, and what separates them."),
            "scenarios": {"type": "array", "title": "Scenarios", "items": _figure_items()},
            "sensitivity_commentary": {
                "type": "string",
                "title": "Sensitivity Commentary",
                "description": "Which assumptions move the valuation most, and by how much.",
            },
        },
    },
    "key_risks": {
        "type": "object",
        "title": "Key Risks",
        "required": ["risks"],
        "properties": {
            "commentary": _commentary("How the risks below interact, and which dominate."),
            "risks": {
                "type": "array",
                "title": "Risks",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "required": ["risk", "why_it_matters"],
                    "properties": {
                        "risk": {"type": "string"},
                        "why_it_matters": {"type": "string"},
                        "early_warning": {"type": "string"},
                        "calculation_id": {"type": "string"},
                        "source_document_id": {"type": "string"},
                    },
                },
            },
        },
    },
    "catalysts": {
        "type": "object",
        "title": "Catalysts",
        "required": ["commentary"],
        "properties": {
            "commentary": _commentary(
                "What could move the shares towards or away from the view, and when."
            ),
            "catalysts": {
                "type": "array",
                "title": "Catalysts",
                "items": {
                    "type": "object",
                    "required": ["label", "expected_timing", "rationale"],
                    "properties": {
                        "label": {"type": "string"},
                        "expected_timing": {"type": "string"},
                        "direction": {"type": "string"},
                        "rationale": {"type": "string"},
                        "source_document_id": {"type": "string"},
                    },
                },
            },
        },
    },
    "prior_research_comparison": {
        "type": "object",
        "title": "Prior Research Comparison",
        "required": ["commentary"],
        "properties": {
            "commentary": _commentary(
                "What earlier approved research on this company concluded, against this run."
            ),
            "comparisons": {
                "type": "array",
                "title": "Comparisons",
                "items": {
                    "type": "object",
                    "required": ["aspect", "prior", "current", "prior_report_id"],
                    "properties": {
                        "aspect": {"type": "string"},
                        "prior": {"type": "string"},
                        "current": {"type": "string"},
                        "prior_report_id": {"type": "string"},
                    },
                },
            },
        },
    },
    "validation_disagreements": {
        "type": "object",
        "title": "Validation & Disagreements",
        "required": ["summary", "validations"],
        "properties": {
            "summary": {
                "type": "string",
                "title": "Summary",
                "description": "What the run's validators measured, in one paragraph.",
            },
            "validations": {
                "type": "array",
                "title": "Validation Metrics",
                "items": {
                    "type": "object",
                    "required": ["metric", "score", "threshold", "verdict"],
                    "properties": {
                        "metric": {"type": "string"},
                        "score": {"type": "string"},
                        "threshold": {"type": "string"},
                        "verdict": {"type": "string"},
                    },
                },
            },
            "disagreements": {
                "type": "array",
                "title": "Disagreements",
                "items": {
                    "type": "object",
                    "required": ["topic", "kind", "resolution", "rationale"],
                    "properties": {
                        "topic": {"type": "string"},
                        "kind": {"type": "string"},
                        "resolution": {"type": "string"},
                        "rationale": {"type": "string"},
                    },
                },
            },
        },
    },
}


# (key, title, position, evidence policy, token budget). Positions are sparse so a custom
# section slots between built-ins without renumbering; the two 0006 rows sit at 100 and
# 200, which is why the spine's blocks run 100s, 200s, 300s, 400s and the deterministic
# appendix at 900s.
_SPINE: list[tuple[str, str, int, dict[str, Any], int]] = [
    ("investment_thesis", "Investment Thesis", 110, _FORWARD_LOOKING_POLICY, 3000),
    ("business_overview", "Business Overview", 120, _EVIDENCE_POLICY, 4000),
    ("segment_analysis", "Segment Analysis", 130, _EVIDENCE_POLICY, 4000),
    ("industry_landscape", "Industry & Competitive Positioning", 140, _EVIDENCE_POLICY, 4000),
    ("management_governance", "Management & Governance", 150, _EVIDENCE_POLICY, 3000),
    ("earnings_quality", "Earnings Quality", 210, _EVIDENCE_POLICY, 4000),
    ("balance_sheet_liquidity", "Balance Sheet & Liquidity", 220, _EVIDENCE_POLICY, 4000),
    ("cash_flow_analysis", "Cash Flow Analysis", 230, _EVIDENCE_POLICY, 4000),
    ("capital_allocation", "Capital Allocation", 240, _EVIDENCE_POLICY, 3000),
    ("growth_outlook", "Growth Outlook", 250, _FORWARD_LOOKING_POLICY, 4000),
    ("valuation_dcf", "Valuation — Discounted Cash Flow", 300, _FORWARD_LOOKING_POLICY, 5000),
    ("scenarios_sensitivities", "Scenarios & Sensitivities", 310, _FORWARD_LOOKING_POLICY, 4000),
    ("key_risks", "Key Risks", 400, _FORWARD_LOOKING_POLICY, 3000),
    ("catalysts", "Catalysts", 410, _FORWARD_LOOKING_POLICY, 3000),
    ("prior_research_comparison", "Prior Research Comparison", 900, _DETERMINISTIC_POLICY, 0),
    ("validation_disagreements", "Validation & Disagreements", 910, _DETERMINISTIC_POLICY, 0),
]


def _section_definitions_table() -> sa.Table:
    return sa.table(
        "section_definitions",
        sa.column("key", sa.Text),
        sa.column("version", sa.Integer),
        sa.column("origin", sa.Text),
        sa.column("title", sa.Text),
        sa.column("position", sa.Numeric),
        sa.column("required", sa.Boolean),
        # ``json``, never JSONB: migration 0007 moved this column to json precisely so
        # the author's declared field order survives storage, and binding the insert
        # through a JSONB construct would normalise the keys before the column ever saw
        # them — the exact bug 0007 fixed, reintroduced at seed time.
        sa.column("output_contract", sa.JSON),
        sa.column("evidence_policy", JSONB),
        sa.column("token_budget", sa.Integer),
        sa.column("allowed_tools", ARRAY(sa.Text)),
        sa.column("applicability", JSONB),
    )


def upgrade() -> None:
    # A deterministic section spends nothing and must be able to say so. Negative budgets
    # stay refused: they are not "free", they are meaningless.
    op.drop_constraint(
        "ck_section_definitions_token_budget_is_positive", "section_definitions", type_="check"
    )
    op.create_check_constraint(
        "ck_section_definitions_token_budget_is_not_negative",
        "section_definitions",
        "token_budget >= 0",
    )

    # As in 0006: dicts are passed as objects, never as json.dumps output, so JSONB stores
    # objects rather than JSON strings. `allowed_tools` is empty for every built-in — the
    # section writer receives its evidence assembled and holds no tools at all.
    op.bulk_insert(
        _section_definitions_table(),
        [
            {
                "key": key,
                "version": 1,
                "origin": "builtin",
                "title": title,
                "position": position,
                "required": True,
                "output_contract": _CONTRACTS[key],
                "evidence_policy": policy,
                "token_budget": budget,
                "allowed_tools": [],
                "applicability": {},
            }
            for key, title, position, policy, budget in _SPINE
        ],
    )

    # Executive summary v2: the 0006 contract plus a citation-carrying figures table.
    op.bulk_insert(
        _section_definitions_table(),
        [
            {
                "key": "executive_summary",
                "version": 2,
                "origin": "builtin",
                "title": "Executive Summary",
                "position": 100,
                "required": True,
                "output_contract": _EXECUTIVE_SUMMARY_V2,
                "evidence_policy": _EVIDENCE_POLICY,
                "token_budget": 2000,
                "allowed_tools": [],
                "applicability": {},
            }
        ],
    )


_EXECUTIVE_SUMMARY_NEW_VERSION = 2


def downgrade() -> None:
    table = _section_definitions_table()
    op.execute(table.delete().where(table.c.key.in_([key for key, *_ in _SPINE])))
    op.execute(
        table.delete().where(
            table.c.key == "executive_summary",
            table.c.version == _EXECUTIVE_SUMMARY_NEW_VERSION,
        )
    )
    op.drop_constraint(
        "ck_section_definitions_token_budget_is_not_negative",
        "section_definitions",
        type_="check",
    )
    op.create_check_constraint(
        "ck_section_definitions_token_budget_is_positive",
        "section_definitions",
        "token_budget > 0",
    )
