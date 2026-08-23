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
    "AttestationKind",
    "ClaimKind",
    "Decision",
    "ExtractionKind",
    "FactBasis",
    "GateKind",
    "Grade",
    "JobStatus",
    "Provider",
    "RequestStatus",
    "SourceTier",
    "TransactionKind",
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


class CatalystOutcomeKind(StrEnum):
    """What an operator recorded about a catalyst whose window closed (K4).

    Three values and no "pending": a pending catalyst simply has no resolution row.
    ``SUPERSEDED`` is for the event that stopped mattering — an acquisition closed a
    different way, a product line was sold — where neither "occurred" nor "did not"
    would be honest.
    """

    OCCURRED = "occurred"
    DID_NOT_OCCUR = "did_not_occur"
    SUPERSEDED = "superseded"


class GateKind(StrEnum):
    """Which human approval gate a decision belongs to.

    ``PLAN`` and ``FINAL`` are the two gates every run passes through. The rest fire
    conditionally: UK filings need their extracted financials confirmed, comparable-company
    analysis needs its peer set confirmed, specialist sectors need an acknowledgement that
    the standard model does not apply, a discounted cash flow needs its assumptions
    confirmed, and a run projected over budget needs a decision.

    ``ASSUMPTIONS`` is the one gate that guards work which has *not* happened yet. Every
    other gate approves something already produced; this one approves the numbers a
    valuation is about to be built on, some of which a model proposed. See ADR 0046.
    """

    PLAN = "PLAN"
    UNMAPPED_CONCEPTS = "UNMAPPED_CONCEPTS"
    PEER_SET = "PEER_SET"
    SECTOR_SPECIALIST = "SECTOR_SPECIALIST"
    THEME_SET = "THEME_SET"
    ASSUMPTIONS = "ASSUMPTIONS"
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
    ECB = "ecb"
    ISSUER_IR = "issuer_ir"
    WEB_SEARCH = "web_search"
    USER_SUPPLIED = "user_supplied"

    INTERNAL_PRIOR_RUN = "internal_prior_run"
    """A prior run's own exported output, fed back in as context.

    Deliberately uncitable: it has no tier mapping (so it resolves to the unverified
    tier) and the citation verifier hard-rejects it regardless (docs/PLAN.md section
    2.8, rule 4). Prior research may inform a hypothesis; it can never support a claim,
    because a platform citing itself would launder yesterday's inference into today's
    evidence.
    """


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


class Grade(StrEnum):
    """How strong the evidence behind an attestation is (ADR 0069).

    Two values, and the whole point of the distinction is that they are *not* both
    second-class. Operator-supplied data is not inherently weaker than a filing — a
    custodian statement is a document with a hash, an extraction and a citation, checked by
    the same verifier that checks a 20-F. What is weaker is a number somebody typed.

    A property of the row, never of a rendering. See :mod:`aer.calc.attestation` for the
    part that cannot be argued with: the grade propagates up a lineage, and a figure whose
    lineage contains an attested node reaches a shareable surface as a type with no field
    for the figure.
    """

    DOCUMENTED = "documented"
    """Extracted from a hashed ``USER_SUPPLIED`` artefact — a contract note, a custodian
    statement, a dividend advice. The full chain applies unchanged: artefact, extraction,
    locator, citation. As citable as a filing."""

    ATTESTED = "attested"
    """Typed by the operator and self-certified, with no artefact behind it.

    Admissible, and marked. It converts, it computes, and every figure above it inherits
    the grade — which is what stops "I will document it later" becoming the default way a
    book gets entered."""


class AttestationKind(StrEnum):
    """Which kind of thing the operator is asserting.

    One value, and that is a statement about what exists rather than a placeholder. A
    subtype here is a value *and* a detail table — ``TRANSACTION`` has ``transactions`` —
    so adding one is visibly a schema change rather than a string. ADR 0069 names two more
    that will arrive when something needs them: a private mark on an unlisted holding, and
    an FX rate the operator typed because no source published one (ADR 0078).
    """

    TRANSACTION = "transaction"


class TransactionKind(StrEnum):
    """What happened to the book.

    Six, and the list is deliberately short for the reason
    :class:`~aer.db.models.security.CorporateActionKind` gives: each needs its own
    arithmetic, and a wrong one is worse than an absent one.

    **A currency exchange is the one ADR 0079 names that is not here.** It is a single
    event touching two currencies, and this row shape holds one — so it would need either a
    second currency column used by nothing else or a pair of rows whose "these two are one
    event" invariant no check constraint in Postgres can see. Getting it wrong means a cash
    balance that double-counts, silently, in the direction that flatters. Until it has a
    shape of its own, exchanging currency is recorded as a withdrawal and a deposit, and
    what is lost is the rate, which was never this table's to assert anyway.
    """

    BUY = "buy"
    """Units in, cash out. Quantity is positive and in units of the security."""

    SELL = "sell"
    """Units out, cash in. Quantity is negative."""

    DIVIDEND = "dividend"
    """Cash received. Quantity is positive and in units of the currency."""

    FEE = "fee"
    """Cash charged — commission, stamp duty, custody. Quantity is negative."""

    DEPOSIT = "deposit"
    """Cash paid into the account. Quantity is positive."""

    WITHDRAWAL = "withdrawal"
    """Cash taken out. Quantity is negative."""


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
