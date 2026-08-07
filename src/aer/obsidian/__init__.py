"""The Obsidian vault: a derived, one-directional projection of approved data.

Postgres and the artefact store are the record; the vault is a view of it that a person
thinks in. The application never reads vault content back as evidence — the projection
only flows outward — and the anti-contamination rules of `docs/PLAN.md` section 2.8 are
enforced in code here before any convenience is layered on: only approved reports export,
every write is contained inside the configured vault root with the personal directory
structurally unreachable, evergreen notes regenerate only above the sentinel, and a prior
run's output can never support a claim (the verifier hard-rejects the
``internal_prior_run`` provider).
"""

from aer.obsidian.export import ObsidianExportError, export_report
from aer.obsidian.notes import (
    SENTINEL,
    CatalystNoteMeta,
    CompanyNoteMeta,
    IndustryNoteMeta,
    RunNoteMeta,
    SourceNoteMeta,
)
from aer.obsidian.vault import VaultWriteError, VaultWriter

__all__ = [
    "SENTINEL",
    "CatalystNoteMeta",
    "CompanyNoteMeta",
    "IndustryNoteMeta",
    "ObsidianExportError",
    "RunNoteMeta",
    "SourceNoteMeta",
    "VaultWriteError",
    "VaultWriter",
    "export_report",
]
