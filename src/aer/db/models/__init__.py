"""ORM models.

Every model is imported here so that ``Base.metadata`` is complete after a single
``import aer.db.models``. Alembic's autogenerate compares the live database against that
metadata, so a model this module forgets to import is silently absent from every
migration — and the resulting missing table only surfaces at runtime.
"""

from __future__ import annotations

from aer.db.models.approval import Approval
from aer.db.models.artefact import Artefact
from aer.db.models.assumption import Assumption
from aer.db.models.audit_event import AuditEvent
from aer.db.models.calculation import Calculation
from aer.db.models.company import Company
from aer.db.models.financial_fact import FinancialFact
from aer.db.models.job import Job
from aer.db.models.job_step import JobStep
from aer.db.models.plan import ResearchPlan
from aer.db.models.request import ResearchRequest
from aer.db.models.source_document import SourceDocument
from aer.db.models.user import User

__all__ = [
    "Approval",
    "Artefact",
    "Assumption",
    "AuditEvent",
    "Calculation",
    "Company",
    "FinancialFact",
    "Job",
    "JobStep",
    "ResearchPlan",
    "ResearchRequest",
    "SourceDocument",
    "User",
]
