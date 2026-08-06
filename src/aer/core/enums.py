"""Domain enumerations.

These live in ``aer.core`` because they are pure vocabulary — no I/O, no dependencies —
and both the database models and the API schemas need them. Defining them here rather
than on the ORM models keeps the correctness core independent of SQLAlchemy.

Each is rendered as a **native PostgreSQL enum**, so an invalid value is rejected by the
database rather than merely by the application. That matters for a system whose whole
premise is that the stored record can be trusted: a bad status written by a script, a
migration or a future service still cannot land.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

__all__ = [
    "CITATION_REQUIRING_CLAIMS",
    "TERMINAL_JOB_STATUSES",
    "AnalysisMode",
    "ClaimKind",
    "Decision",
    "ExtractionKind",
    "FactBasis",
    "GateKind",
    "JobStatus",
    "Provider",
    "RequestStatus",
    "SourceTier",
    "UserRole",
]


class UserRole(StrEnum):
    """Access level. Designed now, enforced when authentication arrives."""

    OWNER = "owner"
    ANALYST = "analyst"
    VIEWER = "viewer"


class AnalysisMode(StrEnum):
    """How much work a research request asks for."""

    QUICK = "quick"
    STANDARD = "standard"
    FULL = "full"


class RequestStatus(StrEnum):
    """Lifecycle of a research request.

    Legal transitions are enforced in the service layer, not by the database. A CHECK
    constraint cannot see the previous value, and a trigger would put business rules
    somewhere they are easy to miss when reading the code. The database's job here is to
    reject values that are not statuses at all.
    """

    DRAFT = "DRAFT"
    PLANNED = "PLANNED"
    APPROVED = "APPROVED"
    RUNNING = "RUNNING"
    AWAITING_REVIEW = "AWAITING_REVIEW"
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class JobStatus(StrEnum):
    """Lifecycle of a job or an individual job step.

    ``PAUSED`` and ``BUDGET_EXCEEDED`` are deliberately distinct from ``FAILED``: neither
    is an error, and both are resumable after a human decision. Collapsing them into
    failure would lose the distinction between "this went wrong" and "this is waiting for
    you", which is the difference between a run you must debug and one you must approve.
    """

    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"

    @property
    def is_terminal(self) -> bool:
        """Whether a run in this state has stopped for good.

        The three that will never execute another step. Everything else — including
        ``AWAITING_APPROVAL`` and ``BUDGET_EXCEEDED``, which look stopped — is a run that
        continues the moment a human decides something, which is why it is a property of
        the vocabulary rather than a set each caller assembles for itself.
        """
        return self in TERMINAL_JOB_STATUSES


# Defined after the class because the members have to exist first. Kept as a frozenset as
# well as a property because several callers ask "which of these jobs are finished?" of a
# query rather than of a single value.
TERMINAL_JOB_STATUSES: frozenset[JobStatus] = frozenset(
    {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED}
)


class GateKind(StrEnum):
    """Which human approval gate a decision belongs to.

    ``PLAN`` and ``FINAL`` are the two gates every run passes through. The rest fire
    conditionally: UK filings need their extracted financials confirmed, comparable-company
    analysis needs its peer set confirmed, specialist sectors need an acknowledgement that
    the standard model does not apply, and a run projected over budget needs a decision.
    """

    PLAN = "PLAN"
    UK_FINANCIALS = "UK_FINANCIALS"
    PEER_SET = "PEER_SET"
    SECTOR_SPECIALIST = "SECTOR_SPECIALIST"
    BUDGET = "BUDGET"
    FINAL = "FINAL"


class Decision(StrEnum):
    """The outcome recorded at an approval gate."""

    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    AMENDED = "AMENDED"


class SourceTier(StrEnum):
    """How much a source may be relied upon.

    The numbers are ordered and load-bearing: **when two sources disagree, the lower tier
    number wins.** That rule is what makes conflict resolution a deterministic comparison
    rather than a judgement call, so it lives in the schema rather than in prose a model
    might weigh differently on different days.

    Two rules follow from the bottom of the table and are enforced elsewhere in code:

    * ``T5_SECONDARY`` is always labelled secondary and is **never the sole support for a
      numeric claim**. A figure that only a newspaper asserts is a figure with no primary
      record behind it.
    * ``T6_UNVERIFIED`` generates hypotheses and is **never citable evidence**. Your own
      earlier notes live here too — a note repeating a number does not make the number
      sourced, it makes it repeated.

    See ``docs/PLAN.md`` section 1.1.
    """

    T1_REGULATORY = "T1_REGULATORY"
    """SEC EDGAR filings and XBRL, FCA NSM, Companies House, RNS. Authoritative for
    reported financials."""

    T2_ISSUER = "T2_ISSUER"
    """Issuer-hosted material: annual report PDFs, results presentations, transcripts.
    Authoritative where tier 1 does not contradict it."""

    T3_OFFICIAL_STATS = "T3_OFFICIAL_STATS"
    """FRED, ONS, Bank of England, BLS, Eurostat, OECD. Authoritative for macro."""

    T4_LICENSED_MARKET = "T4_LICENSED_MARKET"
    """Licensed market data: end-of-day prices, corporate actions. Authoritative for
    prices and returns."""

    T5_SECONDARY = "T5_SECONDARY"
    """Reputable secondary reporting. Never the sole support for a number."""

    T6_UNVERIFIED = "T6_UNVERIFIED"
    """Blogs, forums, user-supplied notes. Hypothesis generation only."""

    @property
    def rank(self) -> int:
        """The tier number. Lower is more authoritative."""
        return int(self.value[1])

    @property
    def is_primary(self) -> bool:
        """Whether this tier counts as a primary source for evidence-policy purposes."""
        return self.rank <= _PRIMARY_TIER_LIMIT

    @property
    def is_citable(self) -> bool:
        """Whether a claim may cite this tier as evidence at all."""
        return self is not SourceTier.T6_UNVERIFIED


# Tiers 1 and 2 are primary: the regulator's copy and the issuer's own copy.
_PRIMARY_TIER_LIMIT = 2


class FactBasis(StrEnum):
    """Which version of a reported number a fact represents.

    The distinction this platform is built around. A single period's revenue has several
    true values depending on when you ask, and conflating them is the mechanism by which a
    backtest flatters itself.

    ``AS_REPORTED`` is the only basis admissible under point-in-time rules, and the only
    one :func:`aer.sources.sec.pit.select_point_in_time` will produce. The other two are
    defined here because they exist in the world and a stored fact has to be able to say
    which it is — not because anything currently creates them.
    """

    AS_REPORTED = "as_reported"
    """What the filing said at the time it was filed. The point-in-time answer."""

    RESTATED = "restated"
    """A later filing's revision of an earlier period. True today, unknowable then."""

    VENDOR_STANDARDISED = "vendor_standardised"
    """A data vendor's recast of the filer's own presentation. Convenient, comparable
    across companies, and traceable to nobody's actual filing — never the sole support
    for a claim."""


class Provider(StrEnum):
    """Where a source document came from.

    Distinct from :class:`SourceTier`: the tier says how much weight a document carries,
    the provider says which adapter fetched it and therefore which licence, rate limit
    and terms of use apply to it. The same publisher can appear at different tiers — an
    issuer's own annual report is tier 2, the same figures inside a regulatory filing are
    tier 1 — so collapsing the two would lose the distinction that matters.
    """

    SEC_EDGAR = "sec_edgar"
    COMPANIES_HOUSE = "companies_house"
    FCA_NSM = "fca_nsm"
    EODHD = "eodhd"
    FRED = "fred"
    ONS = "ons"
    ISSUER_IR = "issuer_ir"
    WEB_SEARCH = "web_search"
    USER_SUPPLIED = "user_supplied"


class ExtractionKind(StrEnum):
    """What an extraction located inside a document.

    The kind governs what a locator means and therefore how a citation to it is checked, which
    is why it is a stored column rather than something inferred from the extractor's name. A
    ``TEXT`` locator is a character range; a ``TABLE`` locator adds a cell reference. An
    extractor can produce both from one document.
    """

    TEXT = "text"
    TABLE = "table"


class ClaimKind(StrEnum):
    """What kind of assertion a sentence in a report makes.

    The kind decides what the sentence must be able to show, so it is recorded rather than
    inferred — §2.9 sets a different bar for each:

    * ``NUMERIC`` and ``FACTUAL`` need at least one **verified** citation.
    * ``FORWARD_LOOKING`` and ``OPINION`` need a stated basis — a calculation, or a premise
      that is itself cited — and are rendered with explicit hedging.

    A ``NUMERIC`` claim additionally has to name the figure it asserts, because no number
    reaches a report unless it is a stored fact or a recorded calculation.
    """

    NUMERIC = "numeric"
    FACTUAL = "factual"
    FORWARD_LOOKING = "forward_looking"
    OPINION = "opinion"


# The kinds that cannot stand on a basis alone. Named here rather than written out at each
# call site, so "which claims need a citation?" has one answer.
CITATION_REQUIRING_CLAIMS: Final[frozenset[ClaimKind]] = frozenset(
    {ClaimKind.NUMERIC, ClaimKind.FACTUAL}
)


class SkillKind(StrEnum):
    """What a user-authored skill file adds to the platform (`docs/PLAN.md` §2.12).

    ``CUSTOM_SECTION`` becomes a report section of its own, with an output contract, an
    evidence policy and a budget. The other three are composed into an existing agent's
    prompt under the ``<user_skill>`` delimiter — methodology guidance, presentation
    preferences, a standing house view — and carry no output contract because they produce
    no section.

    Whatever the kind, a skill is additive-only: it may add requirements and direction,
    never relax the evidence contract. That rule is enforced by the composer in
    :mod:`aer.core.skill_policy`, not by this enum — but the enum is why the composer can
    be exhaustive about what it is composing.
    """

    CUSTOM_SECTION = "custom_section"
    METHODOLOGY = "methodology"
    PREFERENCE = "preference"
    HOUSE_VIEW = "house_view"
