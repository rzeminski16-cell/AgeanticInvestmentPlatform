"""What a report section *is*, as data rather than as code.

**This table is the whole reason a user-defined section will be cheap to add later.** The
product requires that an operator can write their own report section — "how durable is the
moat?" — as a natural-language skill file, and have it appear in the report alongside the
built-in ones. That arrives in Phase 4.

What makes it either cheap or a rewrite is decided *now*, by whether the report's content
model is data or code. If sections are an enum, a hardcoded list, or a template dispatch
table, then adding a user-defined one means touching the workflow, the renderer, the
exporter and the validator. If sections are rows, a user-defined section is another row.

So there is no section enum anywhere in this codebase, and there is a test that scans for
one. The built-in sections are seeded rows with ``origin='builtin'``; a custom section will
be a row with ``origin='skill'``. Everything that iterates sections does so by querying
``report_sections`` ordered by ``position``.

**``output_contract`` is a JSON Schema, and it is what makes the generic renderer
possible.** A section declares the shape of its own output; the renderer walks that shape.
No section needs a bespoke template to produce acceptable Markdown, which is the property
that stops "add a section" from meaning "write a template".

It is stored as ``json`` rather than ``jsonb`` **because the author's field order is part
of the contract** — see the column's own comment. Everything else here is ``jsonb``.

**``position`` is NUMERIC, not an integer.** Sparse ordering: built-ins sit at 100, 200,
300, so a custom section slots in at 250 without renumbering anything. Integer positions
would mean an insert rewrites every row after it, and concurrent inserts would collide.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import ARRAY, CheckConstraint, Index, Numeric, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSON, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from aer.db.base import Base, created_at_column
from aer.db.types import Timestamp, UuidPk

__all__ = ["BUILTIN", "SKILL", "SectionDefinition"]

BUILTIN = "builtin"
"""A section shipped with the platform, seeded by a migration."""

SKILL = "skill"
"""A section defined by a user-authored skill file. Phase 4; the column exists now so the
distinction is in the data from the start rather than being backfilled."""


class SectionDefinition(Base):
    __tablename__ = "section_definitions"

    id: Mapped[UuidPk]

    # 'executive_summary', or 'custom.moat_durability' for a user-defined one. The prefix
    # is a convention rather than a constraint: a namespace keeps a user's section from
    # colliding with a built-in added later.
    key: Mapped[str] = mapped_column(Text, nullable=False)

    # Definitions are versioned and never edited. A report renders against the version
    # pinned at the time it ran, so re-rendering an old report cannot silently apply a
    # section definition nobody had when the analysis was done.
    version: Mapped[int] = mapped_column(nullable=False, default=1, server_default=text("1"))

    origin: Mapped[str] = mapped_column(Text, nullable=False, server_default=BUILTIN)

    # Reserved for the skills table, which does not exist yet. A plain UUID rather than a
    # foreign key for exactly that reason -- the constraint arrives with the table, in the
    # migration that creates it.
    skill_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))

    title: Mapped[str] = mapped_column(Text, nullable=False)

    # Sparse. Built-ins at 100, 200, 300; a custom section slots in between without
    # renumbering anything.
    position: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)

    # A required section that fails fails the report. An optional one is omitted with a
    # note. The difference matters: a missing executive summary is a broken report, a
    # missing optional commentary is a thinner one.
    required: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default=text("false")
    )

    # JSON Schema the section's structured output must satisfy. Also what the generic
    # renderer walks to produce Markdown, which is why no section needs a template.
    #
    # `json`, not `jsonb`. JSONB normalises: it discards key order, reordering by key
    # length and then bytewise. The renderer takes its field order and its table columns
    # from this document, so under JSONB a section declaring `thesis, key_points,
    # key_risks` renders as `thesis, key_risks, key_points` -- the author's order silently
    # replaced by an implementation detail of the storage engine. `json` keeps the text
    # exactly as written. Nothing queries inside this column, so the indexing JSONB buys
    # is worth nothing here.
    output_contract: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    # min_sources, max_tier, requires_primary, allow_forward_looking. Data rather than
    # code so a user-authored section can tighten its own evidence rules -- and, because
    # skill files are additive-only, can never loosen them below the platform floor.
    evidence_policy: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    # A hard per-section cap. One runaway section must not consume a run's whole budget.
    token_budget: Mapped[int] = mapped_column(
        nullable=False, default=4000, server_default=text("4000")
    )

    # A subset of the global tool allowlist, never a superset. Enforced in code when the
    # agent layer reads it: a section that could widen its own permissions would make the
    # allowlist advisory.
    allowed_tools: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")
    )

    # Sector, market and mode predicates deciding whether this section applies to a given
    # request. Empty means always.
    applicability: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    created_at: Mapped[Timestamp] = created_at_column()

    __table_args__ = (
        UniqueConstraint("key", "version", name="uq_section_definitions_key_version"),
        CheckConstraint(f"origin IN ('{BUILTIN}', '{SKILL}')", name="origin_is_known"),
        # A skill-origin section without a skill is a section nobody can trace to its
        # author, and a built-in with one is a category error. Enforced now so the Phase 4
        # work cannot introduce either state.
        CheckConstraint(
            f"(origin = '{SKILL}' AND skill_id IS NOT NULL)"
            f" OR (origin = '{BUILTIN}' AND skill_id IS NULL)",
            name="skill_sections_have_a_skill",
        ),
        CheckConstraint("char_length(btrim(key)) > 0", name="key_is_not_blank"),
        CheckConstraint("token_budget > 0", name="token_budget_is_positive"),
        CheckConstraint(
            "json_typeof(output_contract) = 'object'", name="output_contract_is_an_object"
        ),
        Index("ix_section_definitions_position", "position"),
    )

    def __repr__(self) -> str:
        return f"<SectionDefinition {self.key}@v{self.version} pos={self.position}>"
