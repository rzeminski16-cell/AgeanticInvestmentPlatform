"""A user-authored skill, and every version it has ever had.

Two tables, mirroring the assumptions pattern: ``skills`` is the identity — one row per
key, carrying only what is true of the skill as a whole — and ``skill_versions`` is the
history, one immutable row per save. **Editing creates a version, never rewrites one**,
for the same reason amending an assumption never rewrites the proposal it supersedes: a
run pins the exact version it used (task 36), and a version that could change under a
finished run would make "which instructions shaped this report?" unanswerable.

**A version row exists only for frontmatter that validated.** The service parses and
validates before anything is written, so the acceptance criterion — a skill row cannot
exist with invalid frontmatter — is a property of the write path, and the typed columns
here never hold a value the schema refused.

**The content hash is over the exact source text.** Two versions with one changed byte are
two hashes; a version whose stored fields disagreed with its stored source would be
detectable by re-parsing. That is also what the import diff (threat T20) compares.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aer.db.base import Base, created_at_column
from aer.db.types import Timestamp, UuidFk, UuidPk

if TYPE_CHECKING:
    from aer.db.models.user import User

__all__ = ["Skill", "SkillVersion"]


class Skill(Base):
    """The identity of a skill: its key, its kind, and whether it is enabled."""

    __tablename__ = "skills"

    id: Mapped[UuidPk]

    # The section key a custom section publishes under, and the handle everything refers
    # to the skill by. Unique across the table, whatever the kind.
    key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)

    kind: Mapped[str] = mapped_column(String(32), nullable=False)

    # Disabled skills keep their history and stay resolvable by pinned runs; they are
    # simply never selected for a new plan.
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")

    created_at: Mapped[Timestamp] = created_at_column()

    versions: Mapped[list[SkillVersion]] = relationship(
        back_populates="skill", cascade="all, delete-orphan", order_by="SkillVersion.version"
    )

    __table_args__ = (
        CheckConstraint(
            "kind IN ('custom_section', 'methodology', 'preference', 'house_view')",
            name="kind_is_known",
        ),
    )


class SkillVersion(Base):
    """One saved version of a skill: the validated frontmatter, the body, the hash.

    Immutable by convention and by the service layer — nothing updates these rows. The
    frontmatter fields the platform acts on are typed columns; the nested structures
    (applicability, the output contract, charts) are JSONB that only ever holds what the
    schema validated.
    """

    __tablename__ = "skill_versions"

    id: Mapped[UuidPk]

    skill_id: Mapped[UuidFk] = mapped_column(
        ForeignKey("skills.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # 1, 2, 3... per skill. The author's declared `version` from the frontmatter is not
    # trusted for ordering — see the service — but is stored inside `source`.
    version: Mapped[int] = mapped_column(Integer, nullable=False)

    title: Mapped[str] = mapped_column(Text, nullable=False)
    scope: Mapped[str] = mapped_column(String(64), nullable=False)
    position: Mapped[str | None] = mapped_column(String(80))
    required: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")

    applicability: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    # The *requested* evidence policy, exactly as authored. What a section actually gets
    # is composed at use, against the floor of the day — storing the composed result here
    # would freeze yesterday's floor into tomorrow's runs.
    min_sources: Mapped[int | None] = mapped_column(Integer)
    requires_primary: Mapped[bool | None] = mapped_column(Boolean)
    max_tier: Mapped[int | None] = mapped_column(Integer)
    allow_forward_looking: Mapped[bool | None] = mapped_column(Boolean)

    output_contract: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    token_budget: Mapped[int | None] = mapped_column(Integer)
    allowed_tools: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    charts: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)

    body: Mapped[str] = mapped_column(Text, nullable=False)

    # The file as saved, byte for byte, and its hash. The source is what the editor
    # round-trips and the import diff shows; the hash is what "unchanged" means.
    source: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    created_by: Mapped[UuidFk] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[Timestamp] = created_at_column()

    skill: Mapped[Skill] = relationship(back_populates="versions")
    author: Mapped[User] = relationship()

    __table_args__ = (
        UniqueConstraint("skill_id", "version", name="uq_skill_versions_one_row_per_version"),
        # The same bytes saved twice under one skill is not a new version — the service
        # refuses it, and this is the backstop for a write path that forgot.
        UniqueConstraint(
            "skill_id", "content_hash", name="uq_skill_versions_content_is_versioned_once"
        ),
        CheckConstraint("version >= 1", name="versions_start_at_one"),
        CheckConstraint("token_budget IS NULL OR token_budget >= 1", name="budget_is_positive"),
    )
