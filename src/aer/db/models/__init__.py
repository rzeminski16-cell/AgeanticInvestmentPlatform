"""ORM models.

Every model is imported here so that ``Base.metadata`` is complete after a single
``import aer.db.models``. Alembic's autogenerate compares the live database against that
metadata, so a model this module forgets to import is silently absent from every
migration — and the resulting missing table only surfaces at runtime.
"""

from __future__ import annotations

from aer.db.models.agent_run import AgentRun
from aer.db.models.approval import Approval
from aer.db.models.artefact import Artefact
from aer.db.models.artefact_purge import ArtefactPurge
from aer.db.models.assumption import Assumption
from aer.db.models.assumption_proposal import AssumptionProposal
from aer.db.models.audit_event import AuditEvent
from aer.db.models.calculation import Calculation
from aer.db.models.citation import Citation
from aer.db.models.claim import Claim
from aer.db.models.company import Company
from aer.db.models.cost import Cost
from aer.db.models.disagreement import Disagreement
from aer.db.models.extraction import Extraction
from aer.db.models.financial_fact import FinancialFact
from aer.db.models.job import Job
from aer.db.models.job_cancellation import JobCancellation
from aer.db.models.job_step import JobStep
from aer.db.models.macro import MacroObservationRow, MacroSeriesRow
from aer.db.models.plan import ResearchPlan
from aer.db.models.plan_skill_pin import PlanSkillPin
from aer.db.models.prompt import Prompt
from aer.db.models.report import Report
from aer.db.models.report_section import ReportSection, SectionStatus
from aer.db.models.request import ResearchRequest
from aer.db.models.scenario import Scenario, ScenarioOverride
from aer.db.models.section_definition import SectionDefinition
from aer.db.models.sector_profile import SectorProfile
from aer.db.models.security import (
    CorporateAction,
    CorporateActionKind,
    PriceBar,
    Security,
)
from aer.db.models.sensitivity import Sensitivity, SensitivityCell
from aer.db.models.skill import Skill, SkillVersion
from aer.db.models.source_document import SourceDocument
from aer.db.models.user import User

__all__ = [
    "AgentRun",
    "Approval",
    "Artefact",
    "ArtefactPurge",
    "Assumption",
    "AssumptionProposal",
    "AuditEvent",
    "Calculation",
    "Citation",
    "Claim",
    "Company",
    "CorporateAction",
    "CorporateActionKind",
    "Cost",
    "Disagreement",
    "Extraction",
    "FinancialFact",
    "Job",
    "JobCancellation",
    "JobStep",
    "MacroObservationRow",
    "MacroSeriesRow",
    "PlanSkillPin",
    "PriceBar",
    "Prompt",
    "Report",
    "ReportSection",
    "ResearchPlan",
    "ResearchRequest",
    "Scenario",
    "ScenarioOverride",
    "SectionDefinition",
    "SectionStatus",
    "SectorProfile",
    "Security",
    "Sensitivity",
    "SensitivityCell",
    "Skill",
    "SkillVersion",
    "SourceDocument",
    "User",
]
