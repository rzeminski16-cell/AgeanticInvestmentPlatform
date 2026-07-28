"""One model call: what was asked, what came back, and what it cost.

The provenance record for the non-deterministic half of the system. A calculation has a
formula; a model call has this row — the role, the model the router chose, the prompt
version, the token counts, and **the full request and response archived as artefacts**.

**Both payloads are archived, not summarised.** "Why did it say that?" is the first
question asked about any output that looks wrong, and it is unanswerable from a token count.
The payloads go to the content-addressed store like any other evidence, so they are hashed,
deduplicated, and re-readable months later.

**The role is recorded alongside the model.** The router resolves one to the other, and
recording both means a change to the routing table is visible in the history: two runs of
the same role that used different models say so.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ARRAY, CheckConstraint, ForeignKey, Index, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aer.db.base import Base, created_at_column
from aer.db.types import Timestamp, UuidFk, UuidFkOptional, UuidPk

if TYPE_CHECKING:
    from aer.db.models.job_step import JobStep

__all__ = ["AgentRun"]


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[UuidPk]

    job_step_id: Mapped[UuidFk] = mapped_column(
        ForeignKey("job_steps.id", ondelete="CASCADE"), nullable=False
    )

    # -- Who and what ----------------------------------------------------------------------

    agent_role: Mapped[str] = mapped_column(Text, nullable=False)

    provider: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    effort: Mapped[str | None] = mapped_column(Text)

    prompt_id: Mapped[UuidFkOptional] = mapped_column(ForeignKey("prompts.id", ondelete="SET NULL"))

    # Which skill files shaped this call. Empty until Phase 4; the column exists now so a
    # run performed before skills existed is distinguishable from one that used none.
    skill_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=False, server_default=text("'{}'::uuid[]")
    )

    # -- The exchange, archived ------------------------------------------------------------

    # RESTRICT: the archived payloads must outlive any cleanup that would otherwise remove
    # the artefacts a run's explanation depends on.
    request_payload_ref: Mapped[UuidFkOptional] = mapped_column(
        ForeignKey("artefacts.id", ondelete="RESTRICT")
    )
    response_payload_ref: Mapped[UuidFkOptional] = mapped_column(
        ForeignKey("artefacts.id", ondelete="RESTRICT")
    )

    # -- What it consumed --------------------------------------------------------------------

    input_tokens: Mapped[int | None] = mapped_column()
    output_tokens: Mapped[int | None] = mapped_column()
    cache_read_tokens: Mapped[int | None] = mapped_column()
    cache_write_tokens: Mapped[int | None] = mapped_column()

    # "end_turn", "max_tokens", "refusal". A response truncated at the token limit is not
    # a complete answer, and a section built from one should not be presented as though it
    # were.
    stop_reason: Mapped[str | None] = mapped_column(Text)

    latency_ms: Mapped[int | None] = mapped_column()

    created_at: Mapped[Timestamp] = created_at_column()

    step: Mapped[JobStep] = relationship()

    __table_args__ = (
        CheckConstraint("char_length(btrim(agent_role)) > 0", name="agent_role_is_recorded"),
        CheckConstraint("char_length(btrim(model)) > 0", name="model_is_recorded"),
        CheckConstraint(
            "input_tokens IS NULL OR input_tokens >= 0", name="input_tokens_are_not_negative"
        ),
        CheckConstraint(
            "output_tokens IS NULL OR output_tokens >= 0", name="output_tokens_are_not_negative"
        ),
        Index("ix_agent_runs_job_step_id", "job_step_id"),
        Index("ix_agent_runs_created_at", "created_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<AgentRun {self.agent_role} {self.model} "
            f"in={self.input_tokens} out={self.output_tokens}>"
        )
