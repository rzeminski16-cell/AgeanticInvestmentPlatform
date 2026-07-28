"""A versioned prompt template, hashed.

Prompts are the code of an agentic system in every sense that matters: change one and the
output changes. Treating them as string literals scattered through the source means "why
did this run produce that?" is answerable only if the source has not moved since — which,
months later, it has.

**Content-hashed and immutable by convention.** A new wording is a new version, never an
edit. Every ``agent_run`` records which prompt id it used, so a report's provenance
includes the exact instruction that produced each part of it.
"""

from __future__ import annotations

from sqlalchemy import CheckConstraint, Index, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from aer.db.base import Base, created_at_column
from aer.db.types import Sha256, Timestamp, UuidPk

__all__ = ["Prompt"]


class Prompt(Base):
    __tablename__ = "prompts"

    id: Mapped[UuidPk]

    # "agent.planner", "agent.red_team". Namespaced by the role that uses it.
    key: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[str] = mapped_column(Text, nullable=False)

    template: Mapped[str] = mapped_column(Text, nullable=False)

    # SHA-256 of the template. Lets a run assert that the prompt it is about to use is the
    # one it thinks it is, without comparing whole strings, and makes an accidental edit
    # detectable rather than invisible.
    content_hash: Mapped[Sha256] = mapped_column(nullable=False)

    created_at: Mapped[Timestamp] = created_at_column()

    __table_args__ = (
        UniqueConstraint("key", "version", name="uq_prompts_key_version"),
        CheckConstraint("char_length(content_hash) = 64", name="content_hash_is_sha256"),
        CheckConstraint("char_length(btrim(template)) > 0", name="template_is_not_blank"),
        Index("ix_prompts_key", "key"),
    )

    def __repr__(self) -> str:
        return f"<Prompt {self.key}@{self.version}>"
