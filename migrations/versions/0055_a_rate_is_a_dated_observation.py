"""A rate is a dated observation with a source, not a number in a column.

ADR 0082. ``aer/calc/fx.py`` has shipped complete twice — once under ADR 0026 with no
source at all, once under ADR 0045 with a source and nowhere to put what it returned — and
nothing in ``src/`` has ever called it. This is the table that gives it somewhere.

Shaped like ``macro_observations`` (revision 0016), with three departures, each deliberate:

* The key is a currency **pair** rather than a series id. There is no series to look a rate
  up by, and inventing one per pair would mean inventing one per cross too.
* The evidence is carried in two columns rather than one. ADR 0082 made
  ``source_document_id`` ``NOT NULL`` to keep out a rate with no publication behind it; ADR
  0084 moved that guarantee to ``artefact_sha256`` and let the pointer go nullable with
  ``SET NULL``, the shape ``macro_observations`` and ``price_bars`` already have. A rate has
  to outlive the request that fetched it — the portfolio needs it daily and a published
  report's lineage cites it — and a ``NOT NULL RESTRICT`` on a request-scoped document made
  such a request unpurgeable, permanently. The digest cannot be produced for a response
  nobody fetched, so the door stays shut, and unlike the pointer it stays shut after a
  purge.

**``provider`` gains ``ecb``, which ADR 0045 needed and never got.** `Provider.ECB` has been
in the Python enum since the ECB adapter was written; the Postgres type never learned it, so
a source document for a rate response could not be inserted at all. Nothing noticed, because
Alembic's autogenerate does not compare enum labels — `tests/test_migrations.py` reports no
drift for a type missing a value. The first thing to try writing one was this revision's own
test. Added in an autocommit block, the 0048 pattern: PostgreSQL permits ``ADD VALUE`` inside
a transaction from 12 onwards but refuses to *use* the value in the same one.

Revision ID: 0052
Revises: 0051
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0055"
down_revision = "0054"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE provider ADD VALUE IF NOT EXISTS 'ecb'")

    op.create_table(
        "fx_rates",
        sa.Column(
            "id",
            sa.Uuid(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("base", sa.String(3), nullable=False),
        sa.Column("quote", sa.String(3), nullable=False),
        sa.Column("observed_on", sa.Date(), nullable=False),
        sa.Column("vintage", sa.Date(), nullable=False),
        sa.Column("rate", sa.Numeric(38, 12), nullable=False),
        sa.Column(
            "source_document_id",
            sa.Uuid(),
            sa.ForeignKey("source_documents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("artefact_sha256", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("base", "quote", "observed_on", "vintage", name="uq_fx_rates_point"),
        sa.CheckConstraint("vintage >= observed_on", name="fx_vintage_not_before_observation"),
        sa.CheckConstraint("rate > 0", name="fx_rate_is_positive"),
        sa.CheckConstraint("base <> quote", name="fx_pair_is_two_currencies"),
        sa.CheckConstraint(
            "base = upper(base) AND quote = upper(quote)", name="fx_currencies_are_upper"
        ),
        sa.CheckConstraint(
            "char_length(base) = 3 AND char_length(quote) = 3", name="fx_currencies_are_iso_codes"
        ),
        sa.CheckConstraint("char_length(artefact_sha256) = 64", name="fx_sha256_is_full_length"),
        sa.CheckConstraint(
            "artefact_sha256 = lower(artefact_sha256)", name="fx_sha256_is_lowercase"
        ),
    )
    # "This pair, as at that date", which is every read this table serves. Descending on
    # vintage because the answer is always the newest reading of the chosen day.
    op.create_index(
        "ix_fx_rates_pit",
        "fx_rates",
        ["base", "quote", "observed_on", sa.text("vintage DESC")],
    )


def downgrade() -> None:
    op.drop_table("fx_rates")
    # The enum value stays, as it does in revisions 0016 and 0025. Postgres cannot drop
    # one, and recreating the type would mean rewriting every column that uses it — a far
    # larger operation than this migration was.
