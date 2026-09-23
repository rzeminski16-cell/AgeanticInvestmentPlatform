"""A run knows it is a refresh, and what moved is a row (F4, ADR 0131).

Data model Gap 2, as built. ``jobs`` gains its kind — ``full`` or ``refresh`` — the report
a refresh updates, and the moment the operator read its change summary. ``report_changes``
is the diff: one row per material move between the prior report's run and the refresh,
written by code from the two runs' own figures, never by a model. And ``change_summary``
is seeded as the section at the head of a refreshed document, platform-filled from those
rows, applicable to no request (its predicate names a property every request answers
``false``) and created by the refresh's carry step alone.

The downgrade drops the table and the three columns, and removes the section definition
unless a stored report cites it.

Revision ID: 0086
Revises: 0085
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

revision = "0086"
down_revision = "0085"
branch_labels = None
depends_on = None

_KEY = "change_summary"
_TITLE = "What changed"
# Ahead of everything the spine seeds (the executive summary sits at 100): the headline
# deliverable of a refresh is what moved, not the report.
_POSITION = 50

# Platform-filled fields first, in the mechanism's order (08-mechanisms §1.5), then the one
# field the model writes. The rows are the record; the commentary is prose around them.
_CONTRACT: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["commentary"],
    "properties": {
        "basis": {
            "type": "string",
            "platform_filled": True,
            "description": "Which report this refresh updated, as at which date, and what it read.",
        },
        "broke": {
            "type": "array",
            "platform_filled": True,
            "description": (
                "Premises whose predicate changed verdict, first because they demand a decision."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "prior": {"type": "string"},
                    "new": {"type": "string"},
                    "unit": {"type": "string"},
                    "change_pct": {"type": "string"},
                    "narrative": {"type": "string"},
                    "calculation_id": {"type": ["string", "null"]},
                    "financial_fact_id": {"type": ["string", "null"]},
                },
            },
        },
        "moved": {
            "type": "array",
            "platform_filled": True,
            "description": "Figures over the materiality threshold, largest relative change first.",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "prior": {"type": "string"},
                    "new": {"type": "string"},
                    "unit": {"type": "string"},
                    "change_pct": {"type": "string"},
                    "movement": {"type": "string"},
                    "narrative": {"type": "string"},
                    "calculation_id": {"type": ["string", "null"]},
                    "financial_fact_id": {"type": ["string", "null"]},
                },
            },
        },
        "new_documents": {
            "type": "array",
            "platform_filled": True,
            "description": "Filings read for the first time.",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "form": {"type": "string"},
                    "published": {"type": "string"},
                    "source_document_id": {"type": ["string", "null"]},
                },
            },
        },
        "unchanged": {
            "type": "string",
            "platform_filled": True,
            "description": "One line on how much did not move.",
        },
        "sections": {
            "type": "object",
            "platform_filled": True,
            "description": "Which sections were re-drafted, carried forward, or carried stale.",
            "properties": {
                "redrafted": {"type": "array", "items": {"type": "string"}},
                "carried": {"type": "array", "items": {"type": "string"}},
                "stale": {"type": "array", "items": {"type": "string"}},
            },
        },
        "commentary": {
            "type": "string",
            "description": (
                "Two or three plain sentences on what the moves above amount to for a reader "
                "of the prior report, resting only on the rows shown. Nothing about what to "
                "do; no rating, no target, no figure the rows do not carry."
            ),
        },
    },
}

_POLICY: dict[str, Any] = {
    "min_sources": 0,
    "requires_primary": False,
    "max_tier": "T5_SECONDARY",
    "allow_forward_looking": False,
    "concept_priority": [],
    "excerpt_keywords": [],
    "word_budget": 200,
}


def _section_definitions_table() -> sa.Table:
    return sa.table(
        "section_definitions",
        sa.column("key", sa.Text),
        sa.column("version", sa.Integer),
        sa.column("origin", sa.Text),
        sa.column("title", sa.Text),
        sa.column("position", sa.Numeric),
        sa.column("required", sa.Boolean),
        # ``json``, never JSONB: the renderer takes its field order from this document.
        sa.column("output_contract", sa.JSON),
        sa.column("evidence_policy", JSONB),
        sa.column("token_budget", sa.Integer),
        sa.column("allowed_tools", ARRAY(sa.Text)),
        sa.column("applicability", JSONB),
    )


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column("refresh_kind", sa.String(16), nullable=False, server_default=sa.text("'full'")),
    )
    op.add_column("jobs", sa.Column("refreshes_report_id", sa.Uuid(), nullable=True))
    # Named, and added after the column: reports already point at jobs, and a cycle of
    # unnamed constraints is one the metadata cannot sort.
    op.create_foreign_key(
        "fk_jobs_refreshes_report_id",
        "jobs",
        "reports",
        ["refreshes_report_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column("jobs", sa.Column("changes_read_at", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint(
        "job_refresh_kind_is_one_of_two", "jobs", "refresh_kind IN ('full', 'refresh')"
    )
    # One direction only: a full run names no report, and a refresh whose report is later
    # deleted keeps its kind with the reference set null, rather than failing the delete.
    op.create_check_constraint(
        "job_full_run_names_no_report",
        "jobs",
        "refresh_kind = 'refresh' OR refreshes_report_id IS NULL",
    )

    op.create_table(
        "report_changes",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "job_id", sa.Uuid(), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "prior_report_id",
            sa.Uuid(),
            sa.ForeignKey("reports.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "report_id", sa.Uuid(), sa.ForeignKey("reports.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("period", sa.String(32), nullable=True),
        sa.Column("case", sa.String(32), nullable=True),
        sa.Column("prior_value", sa.Numeric(38, 12), nullable=True),
        sa.Column("prior_unit", sa.String(32), nullable=True),
        sa.Column("new_value", sa.Numeric(38, 12), nullable=True),
        sa.Column("new_unit", sa.String(32), nullable=True),
        sa.Column("change_pct", sa.Numeric(18, 6), nullable=True),
        sa.Column("material", sa.Boolean(), nullable=False),
        sa.Column("movement", sa.String(16), nullable=False),
        sa.Column("narrative", sa.Text(), nullable=False),
        sa.Column("prior_reference", sa.Text(), nullable=True),
        sa.Column("new_reference", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "kind IN ('fact', 'calculation', 'document', 'premise')",
            name="report_change_kind_is_one_of_four",
        ),
        sa.CheckConstraint(
            "char_length(btrim(narrative)) > 0", name="report_change_says_something"
        ),
    )
    op.create_index("ix_report_changes_job_id", "report_changes", ["job_id"])
    op.create_index("ix_report_changes_prior_report_id", "report_changes", ["prior_report_id"])

    op.bulk_insert(
        _section_definitions_table(),
        [
            {
                "key": _KEY,
                "version": 1,
                "origin": "builtin",
                "title": _TITLE,
                "position": _POSITION,
                "required": False,
                "output_contract": _CONTRACT,
                "evidence_policy": _POLICY,
                "token_budget": 1200,
                "allowed_tools": [],
                # A request is never a refresh (`ResearchRequest.is_refresh`), so no full
                # run resolves this row; the refresh's carry step creates it by name.
                "applicability": {"is_refresh": [True]},
            }
        ],
    )


def _refuse_if_a_report_cites_it(bind: sa.Connection) -> None:
    cited = bind.execute(
        sa.text(
            "SELECT count(*) FROM report_sections rs "
            "JOIN section_definitions sd ON sd.id = rs.section_definition_id "
            "WHERE sd.key = :key AND sd.origin = 'builtin'"
        ),
        {"key": _KEY},
    ).scalar_one()
    if cited:
        message = (
            f"{cited} stored report section(s) cite {_KEY!r}, so its definition cannot be "
            "removed. Delete those runs first (`aer reset-research`), or leave the row."
        )
        raise RuntimeError(message)


def downgrade() -> None:
    bind = op.get_bind()
    _refuse_if_a_report_cites_it(bind)
    bind.execute(
        sa.text("DELETE FROM section_definitions WHERE key = :key AND origin = 'builtin'"),
        {"key": _KEY},
    )
    op.drop_table("report_changes")
    op.drop_constraint("job_full_run_names_no_report", "jobs", type_="check")
    op.drop_constraint("job_refresh_kind_is_one_of_two", "jobs", type_="check")
    op.drop_column("jobs", "changes_read_at")
    op.drop_constraint("fk_jobs_refreshes_report_id", "jobs", type_="foreignkey")
    op.drop_column("jobs", "refreshes_report_id")
    op.drop_column("jobs", "refresh_kind")
