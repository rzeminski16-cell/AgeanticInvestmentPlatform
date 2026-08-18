"""Evidence knows whose it is.

ADR 0061. A request could only ever touch one company, so ``request_id`` was a working
proxy for "about the subject" everywhere a fact or a source document was selected. Peer
acquisition ended that: the first complete run fetched eight peers' filings under the
subject's request, and an Amazon research note cited Walmart, Alibaba, eBay, JD.com,
MercadoLibre and Target as its evidence.

Two columns, both nullable, both SET NULL:

``research_requests.company_id`` — the subject, written by ``acquire`` once the ticker is
resolved against a registry. NULL before that, which is a real state rather than a missing
one: a request is written from a string somebody typed and no company exists for it yet.

``source_documents.company_id`` — which issuer a document is about, where it is about one.
NULL for a macro series or a regulator's note, and those stay visible to every run that
fetched them.

Existing rows are backfilled where the answer is unambiguous. A source document's company
is read from the facts parsed out of it — every fact already carries ``company_id`` — and a
document whose facts disagree, or which produced none, is left NULL. A request's company is
read from the same place. Left NULL rather than guessed: this migration must not be the
step that invents an attribution, and a NULL source document is visible to its own run
either way.

Revision ID: 0042
Revises: 0041
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "research_requests",
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_research_requests_company_id",
        "research_requests",
        "companies",
        ["company_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.add_column(
        "source_documents",
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_source_documents_company_id",
        "source_documents",
        "companies",
        ["company_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_source_documents_request_id_company_id",
        "source_documents",
        ["request_id", "company_id"],
    )

    bind = op.get_bind()

    # A document's issuer, where its own facts agree on one. `HAVING COUNT(DISTINCT ...) = 1`
    # is the whole safety argument: a document that produced facts for two companies is not
    # a document this migration can attribute, and it is left NULL.
    bind.execute(
        sa.text(
            "UPDATE source_documents AS sd SET company_id = agreed.company_id "
            "FROM ("
            # `array_agg(DISTINCT ...)` rather than an aggregate over the value, because
            # Postgres has no `min(uuid)` — and because taking element one of the distinct
            # set says what the HAVING clause has already established: there is exactly one.
            "  SELECT source_document_id, (array_agg(DISTINCT company_id))[1] AS company_id"
            "  FROM financial_facts"
            "  GROUP BY source_document_id"
            "  HAVING COUNT(DISTINCT company_id) = 1"
            ") AS agreed "
            "WHERE sd.id = agreed.source_document_id"
        )
    )

    # A request's subject, matched on ticker and exchange.
    #
    # **Not the documents-agree rule used above, and deliberately.** A request that ran with
    # a peer set holds documents for nine companies, so "the documents agree" answers NULL —
    # and a NULL subject makes every fact query on that request return nothing, which would
    # blank the report the operator already has. Ticker and exchange is exactly how
    # `research._company_id_for` has always resolved the subject, so this is the historical
    # rule applied once to historical rows rather than a new dependency on a weak key: from
    # here on, `acquire` writes the id it resolved.
    bind.execute(
        sa.text(
            "UPDATE research_requests AS r SET company_id = c.id "
            "FROM companies AS c "
            "WHERE r.company_id IS NULL"
            "  AND c.ticker = r.ticker"
            "  AND c.exchange = r.exchange"
        )
    )


def downgrade() -> None:
    op.drop_index("ix_source_documents_request_id_company_id", table_name="source_documents")
    op.drop_constraint("fk_source_documents_company_id", "source_documents", type_="foreignkey")
    op.drop_column("source_documents", "company_id")
    op.drop_constraint("fk_research_requests_company_id", "research_requests", type_="foreignkey")
    op.drop_column("research_requests", "company_id")
