"""One act of exporting an approved report to the Obsidian vault.

A record of the *act*, not the content: the vault is a derived projection and the notes
themselves are deterministic from the database state, so what this row adds is when the
projection was last refreshed, which files it touched, and which generator wrote them —
enough for the report page to answer "is the vault current?" honestly.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aer.db.base import Base, created_at_column
from aer.db.types import Timestamp, UuidFk, UuidPk

if TYPE_CHECKING:
    from aer.db.models.report import Report

__all__ = ["ObsidianExport"]


class ObsidianExport(Base):
    __tablename__ = "obsidian_exports"

    id: Mapped[UuidPk]

    report_id: Mapped[UuidFk] = mapped_column(
        ForeignKey("reports.id", ondelete="CASCADE"), nullable=False, index=True
    )

    exported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Vault-relative paths in the order written. The vault root they resolve against is
    # configuration, deliberately not recorded: a moved vault is the same export.
    files: Mapped[list[str]] = mapped_column(JSONB, nullable=False)

    generator_version: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped[Timestamp] = created_at_column()

    report: Mapped[Report] = relationship()

    def __repr__(self) -> str:
        return f"<ObsidianExport report={self.report_id} files={len(self.files)}>"
