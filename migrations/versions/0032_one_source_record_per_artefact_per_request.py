"""One source-document record per artefact per request, enforced by the database.

The live AAPL run held two source rows for one digest of its own 10-Q. The A43 pre-read
closed the sequential duplicate, but the five research steps run as parallel nodes, each
with its own session, and neither can see the other's uncommitted insert — the classic
check-then-write race. The database is the only participant that sees both writers, so
the database holds the rule.

Existing duplicates are merged before the constraint lands. The keeper is the request's
best record of the digest — unquarantined first, then the strongest tier, then the
earliest retrieval, which is the acquire step's — matching the preference the A43 read
path already applies. Every table that references a losing row is repointed to the
keeper before the loser is deleted: two of those references RESTRICT deletes, so an
unrepointed loser would fail the migration rather than corrupt anything. References
carried inside JSONB content (a rendered section naming a source id) are not rewritten;
a report from before the merge may show an unresolvable reference, which the provenance
viewer reports honestly rather than hiding.

The downgrade drops only the constraint. Merged rows cannot be unmerged — the losers are
gone, and refabricating them would be inventing provenance.

Revision ID: 0032
Revises: 0031
"""

from __future__ import annotations

from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None

# Every table holding a source_document_id foreign key. citations and financial_facts
# RESTRICT deletes; the rest would nullify or cascade, which for a merge is still wrong —
# the reference is not gone, it belongs to the keeper.
_REFERENCING_TABLES = (
    "citations",
    "extractions",
    "financial_facts",
    "price_bars",
    "corporate_actions",
    "macro_observations",
)

# The keeper per (request_id, artefact_id): unquarantined first, then the strongest tier
# (the enum's declaration order runs T1 to T6), then the earliest retrieval, with the id
# as a deterministic tiebreak.
_DUPLICATES = """
    SELECT id, keeper_id FROM (
        SELECT
            id,
            row_number() OVER w AS rn,
            first_value(id) OVER w AS keeper_id
        FROM source_documents
        WINDOW w AS (
            PARTITION BY request_id, artefact_id
            ORDER BY quarantined ASC, source_tier ASC, retrieved_at ASC, id ASC
            ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
        )
    ) ranked
    WHERE rn > 1
"""


def upgrade() -> None:
    for table in _REFERENCING_TABLES:
        op.execute(
            f"UPDATE {table} SET source_document_id = duplicates.keeper_id "  # noqa: S608
            f"FROM ({_DUPLICATES}) AS duplicates "
            f"WHERE {table}.source_document_id = duplicates.id"
        )
    op.execute(f"DELETE FROM source_documents WHERE id IN (SELECT id FROM ({_DUPLICATES}) AS d)")  # noqa: S608
    op.create_unique_constraint(
        "uq_source_document_per_artefact", "source_documents", ["request_id", "artefact_id"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_source_document_per_artefact", "source_documents", type_="unique")
