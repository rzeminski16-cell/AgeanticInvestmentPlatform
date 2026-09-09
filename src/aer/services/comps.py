"""The peer set a person agreed to, and the table built from it.

**A peer set nobody confirmed is a comparison nobody can defend.** A badly chosen peer moves a
median more than most modelling choices do, and it moves it invisibly: the multiple is
computed correctly from a company that should not have been in the set. So the peer set goes
through the ``PEER_SET`` gate, and :func:`build` refuses to produce a table without an
approval — refuses, rather than producing an unlabelled one, because a table that exists is a
table that gets read.

The refusal is a **type**, not a check. :func:`build` returns a
:class:`~aer.calc.comps.CompsTable`, and there is no code path that constructs one from an
unconfirmed set. That is the same argument ADR 0029 made for the sector block: a rule enforced
by what a function can return is one a later caller cannot forget.

**What may leave the machine is a licence question, and the answer travels with the table.**
Every multiple here is computed from a price that arrived under a subscription, so whether a
computed figure may be published is not this module's judgement to make. It reads the
determination off the provider's :class:`~aer.fetch.policy.FetchPolicy` and puts it on the
table, where :meth:`~aer.calc.comps.CompsTable.for_audience` acts on it. For EODHD the
operator determined on 2026-08-09 that derived figures may be published (ADR 0030, amended),
so a shareable audience now receives the multiples; the *series* and any chart of it remain
internal, because those are the information in repackaged form and the terms are not
ambiguous about them.

The licence note recorded on the table is the terms' own wording plus the determination that
was made about them, rather than a summary of either.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Final

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.calc import comps as calc
from aer.calc.engine import CalculationContext
from aer.calc.units import Quantity
from aer.core.enums import Decision, GateKind, Provider
from aer.core.hashing import canonical_json, sha256_hex
from aer.db.models import (
    Approval,
    Company,
    FinancialFact,
    Job,
    JobStep,
    OperatorPeer,
    User,
)
from aer.errors import AerError, ValidationError
from aer.fetch.policy import DEFAULT_POLICIES

__all__ = [
    "ADDABLE_LIMIT",
    "MAX_PROPOSED_PEERS",
    "PEER_SET_STEP",
    "SIC_PREFIX",
    "PeerProposal",
    "PeerSetNotConfirmedError",
    "add_operator_peer",
    "addable_companies",
    "band_for",
    "build",
    "confirmed_peer_set",
    "gate_payload_for_job",
    "operator_peers_for_job",
    "payload_for_job",
    "peer_set_payload",
    "peer_set_required",
    "propose_peers_from_sic",
    "specialist_multiples_for",
]

_log = structlog.get_logger("aer.services.comps")

PEER_SET_STEP: Final = "propose_peers"
"""The workflow step whose output carries the proposal. One name, used by both halves."""


# Why a peer recorded by name alone contributes no multiple today. One constant so the
# gate page, the report and the run payload all say it identically — a dated peer whose
# pricing failed gets a different reason, in ``build``, because for that one the filings
# *are* held.
#
# **Written to survive the report's register** (gap R20). It reaches a reader through the
# comps disclosure as well as an operator through the gate, and the first draft named
# ADR 0059 inside it — which is the architecture decision that settled this, and is exactly
# the process language `presentation_integrity` refuses in a document that should be about a
# company. The decision belongs in this comment; the sentence belongs to the reader.
UNACQUIRED_PEER_REASON: Final = (
    "this research holds no filings and no price series for it, and a peer multiple needs both"
)


class PeerSetNotConfirmedError(AerError):
    """A comps table was asked for and nobody has agreed to the peer set.

    Its own class because the fix is a human action rather than a corrected argument, and the
    message names the gate so an operator knows which one.
    """

    code = "peer_set_not_confirmed"
    http_status = 409


@dataclass(frozen=True, slots=True)
class PeerProposal:
    """One proposed comparable, and why.

    ``rationale`` is required in substance if not in type: :func:`peer_set_payload` keeps
    whatever the proposer wrote, and a blank one is what a reviewer at the gate is meant to
    notice. A peer with no stated reason for being there is the one most likely to be wrong.
    """

    identifier: str
    name: str
    rationale: str
    # The latest reported period this platform holds for the peer, or ``None`` for one
    # recorded by name alone — resolved against the registry but never fetched, which is
    # every newly proposed peer since ADR 0059 was amended: acquiring a peer's filings
    # bought nothing a comps table could use without its prices, and the acquisition was
    # what let eight issuers' facts into the subject's evidence pool (ADR 0061).
    period_end: date | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "identifier": self.identifier,
            "name": self.name,
            "rationale": self.rationale,
            # Empty string rather than None, so the gate payload — and therefore the
            # approval hash — is built from strings the same way in both states.
            "period_end": self.period_end.isoformat() if self.period_end else "",
        }


def peer_set_payload(produced: Mapping[str, Any]) -> dict[str, Any]:
    """Exactly what the peer-set gate approves, as one structure.

    Built from the proposal step's own output, so the peers an operator confirms are the peers
    that were proposed rather than a re-derivation that might differ. Hashed into the
    approval, so confirming one set and running another is refused.
    """
    peers = produced.get("peers", [])
    return {
        "subject": str(produced.get("subject", "")),
        "subject_period_end": str(produced.get("subject_period_end", "")),
        "basis": str(produced.get("basis", "")),
        "peers": [
            {
                "identifier": str(peer.get("identifier", "")),
                "name": str(peer.get("name", "")),
                "rationale": str(peer.get("rationale", "")),
                "period_end": str(peer.get("period_end", "")),
            }
            for peer in peers
            if isinstance(peer, Mapping)
        ],
        "proposed_by": str(produced.get("proposed_by", "")),
    }


def peer_set_required(produced: Mapping[str, Any]) -> bool:
    """Whether this run needs a person to agree its peer set.

    **On a non-empty proposal.** A run that proposed no peers has no comparison to defend and
    should not wait at a gate to confirm an empty list; it simply produces no comps table, and
    the report says so.
    """
    return bool(peer_set_payload(produced)["peers"])


# How much of a SIC code two companies must share to be candidate peers. Four digits is the
# full code and too strict — it would propose nobody for most companies. Two is the major
# group, which is a real industry: 35xx is machinery, 60xx is depository institutions. The
# proposal is a starting point for a person, not an answer.
SIC_PREFIX: Final = 2

# The most peers a proposal puts forward. A reviewer confirming twenty companies is a
# reviewer clicking through, and a peer set nobody actually read is the thing this gate
# exists to prevent.
MAX_PROPOSED_PEERS: Final = 8

# How many companies the add-a-peer picker offers. A list nobody scrolls is a list
# nobody reads, and the pool grows with every run.
ADDABLE_LIMIT: Final = 200


async def propose_peers_from_sic(
    session: AsyncSession,
    *,
    subject: Company,
    as_of: date,
    limit: int = MAX_PROPOSED_PEERS,
) -> tuple[PeerProposal, ...]:
    """Companies already in this database sharing the subject's industry group.

    **Deterministic, free and reproducible, which makes it the right floor — and it is now
    a floor rather than the whole proposal.** ADR 0059's `peer_proposal` role names
    comparables with a written rationale and `aer.services.peer_discovery` resolves and
    acquires them; this runs alongside, and its entries are merged in underneath the
    model's. It still costs nothing and still works when the model call fails, which is
    what a fallback is for.

    A fresh database proposes nobody *from here*, and that is the honest answer for this
    lookup: the platform has not researched a comparable company yet, so it holds none. It
    used to be the answer for the whole step, which is why no run ever produced a comps
    table until a second proposer existed.

    A candidate with no stored financial facts is skipped, because a peer with no period end
    cannot be aligned against the subject and would be excluded a step later anyway.
    """
    if not subject.sic or len(subject.sic) < SIC_PREFIX:
        return ()

    group = subject.sic[:SIC_PREFIX]
    candidates = await session.scalars(
        select(Company)
        .where(
            Company.id != subject.id,
            Company.sic.is_not(None),
            Company.sic.startswith(group),
        )
        .order_by(Company.name)
        .limit(limit)
    )

    proposals: list[PeerProposal] = []
    for candidate in candidates:
        period_end = await session.scalar(
            select(func.max(FinancialFact.period_end)).where(
                FinancialFact.company_id == candidate.id,
                FinancialFact.period_end <= as_of,
            )
        )
        if period_end is None:
            continue
        proposals.append(
            PeerProposal(
                identifier=str(candidate.id),
                name=candidate.name,
                rationale=(
                    f"Shares SIC group {group} with the subject "
                    f"({candidate.sic} against {subject.sic}), and this platform holds "
                    f"financial facts for it to {period_end.isoformat()}. Proposed by code "
                    "from the industry classification alone — whether it is genuinely "
                    "comparable is the judgement this gate asks for."
                ),
                period_end=period_end,
            )
        )

    return tuple(proposals)


async def confirmed_peer_set(session: AsyncSession, job: Job) -> tuple[PeerProposal, ...]:
    """The peers this run may compare against, and nothing else.

    Raises:
        PeerSetNotConfirmedError: If peers were proposed and the ``PEER_SET`` gate has not
            approved them, or if the approval was recorded against a different set. Both are
            refusals rather than empty results, because an empty comps table and a withheld
            one read identically and mean opposite things.
    """
    # The whole set, additions included: the operator approved what the page showed, and
    # the page shows both halves. Verifying against the proposal alone would accept an
    # approval of a set nobody was ever offered.
    payload = await payload_for_job(session, job.id)

    if not payload["peers"]:
        return ()

    approval = await session.scalar(
        select(Approval)
        .where(
            Approval.job_id == job.id,
            Approval.gate == GateKind.PEER_SET,
            Approval.decision == Decision.APPROVED,
        )
        .order_by(Approval.decided_at.desc())
        .limit(1)
    )
    if approval is None:
        message = (
            f"This run proposed {len(payload['peers'])} peer(s) and nobody has confirmed "
            "them. A badly chosen peer moves a median more than most modelling choices and "
            "does it invisibly, so the set is not applied on a model's say-so. Decide at the "
            "PEER_SET gate."
        )
        raise PeerSetNotConfirmedError(
            message, context={"job_id": str(job.id), "gate": GateKind.PEER_SET.value}
        )

    expected = sha256_hex(canonical_json(payload))
    if approval.payload_hash and approval.payload_hash != expected:
        message = (
            "The confirmed peer set for this run does not match the one on record. Somebody "
            "approved a different set of comparables, so the approval is not an approval of "
            "this one."
        )
        raise PeerSetNotConfirmedError(
            message,
            context={
                "job_id": str(job.id),
                "approved": approval.payload_hash,
                "current": expected,
            },
        )

    return tuple(
        PeerProposal(
            identifier=peer["identifier"],
            name=peer["name"],
            rationale=peer["rationale"],
            period_end=(date.fromisoformat(peer["period_end"]) if peer["period_end"] else None),
        )
        for peer in payload["peers"]
    )


async def gate_payload_for_job(session: AsyncSession, job_id: uuid.UUID) -> dict[str, Any]:
    """What the peer-set review page shows, read back from the proposal step.

    Returns an empty payload for a run that has not proposed yet, so a page can render
    "nothing to review" rather than an error.
    """
    step = await _proposal_step(session, job_id)
    if step is None or not step.output_ref:
        return {}
    return await payload_for_job(session, job_id)


async def _proposal_step(session: AsyncSession, job_id: uuid.UUID) -> JobStep | None:
    step: JobStep | None = await session.scalar(
        select(JobStep)
        .where(JobStep.job_id == job_id, JobStep.step_key == PEER_SET_STEP)
        .order_by(JobStep.sequence.desc())
        .limit(1)
    )
    return step


async def operator_peers_for_job(
    session: AsyncSession, job_id: uuid.UUID, *, as_of: date
) -> list[PeerProposal]:
    """The companies the operator put on this run's peer set.

    Built exactly as the deterministic floor builds its own — an identifier, the
    registry's name rather than anything typed, and the latest period this platform holds
    for the company. A row whose company has no facts on or before the as-of date yields
    no period end and is dropped here for the reason the floor drops one: a peer with no
    period end cannot be aligned against the subject and would be excluded a step later.
    """
    rows = await session.scalars(
        select(OperatorPeer)
        .where(OperatorPeer.job_id == job_id)
        .order_by(OperatorPeer.created_at, OperatorPeer.id)
    )
    proposals: list[PeerProposal] = []
    for row in rows:
        company = await session.get(Company, row.company_id)
        if company is None:  # pragma: no cover -- CASCADE prevents this
            continue
        period_end = await session.scalar(
            select(func.max(FinancialFact.period_end)).where(
                FinancialFact.company_id == company.id,
                FinancialFact.period_end <= as_of,
            )
        )
        if period_end is None:
            continue
        proposals.append(
            PeerProposal(
                identifier=str(company.id),
                name=company.name,
                rationale=row.rationale,
                period_end=period_end,
            )
        )
    return proposals


async def payload_for_job(session: AsyncSession, job_id: uuid.UUID) -> dict[str, Any]:
    """The gate's payload over the whole set — the step's proposal and the operator's.

    **One funnel**, for the reason the theme gate has one: the page renders this, the
    approval hashes this, and `confirmed_peer_set` verifies against this, so the three
    cannot disagree about what was agreed to. Adding a peer after approving changes the
    payload and invalidates the approval, which is the stale-approval rule working.
    """
    step = await _proposal_step(session, job_id)
    produced = (step.output_ref or {}) if step is not None else {}
    payload = peer_set_payload(produced)
    as_of = _as_of_from(payload)
    added = await operator_peers_for_job(session, job_id, as_of=as_of)
    if not added:
        return payload
    known = {peer["identifier"] for peer in payload["peers"]}
    payload["peers"] = payload["peers"] + [
        peer.as_dict() for peer in added if peer.identifier not in known
    ]
    return payload


def _as_of_from(payload: Mapping[str, Any]) -> date:
    """The subject's period end, which bounds a peer's own — or today if it is missing.

    The floor bounds its candidates by the run's as-of date; this bounds them by what the
    payload already carries, so a peer added to a run about a past period is aligned the
    same way the proposed ones were.
    """
    raw = str(payload.get("subject_period_end", ""))
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return datetime.now(UTC).date()


async def add_operator_peer(
    session: AsyncSession, *, job: Job, company_id: uuid.UUID, rationale: str, actor: User
) -> OperatorPeer:
    """Put one company of the operator's own on this run's peer set.

    **A company this platform already holds, not a ticker to go and resolve.** The web
    process has no source client and should not have one: only `aer.fetch` reaches the
    network and acquisition is the worker's. That is not a restriction invented here —
    the deterministic floor draws from exactly this pool, and a company with no stored
    facts could not be aligned against the subject in any case.

    An addition, not a confirmation: it joins the set the gate is about to hash, and the
    gate's approval is still what admits it to a comps table.

    Raises:
        ValidationError: If the rationale is blank, if there is no such company, if it is
            the subject itself, or if this run's set already carries it.
    """
    if not rationale.strip():
        message = (
            "A peer needs a reason. A badly chosen one moves a median more than most "
            "modelling choices do and does it invisibly, which is what this gate exists "
            "to catch."
        )
        raise ValidationError(message, context={"job_id": str(job.id)})

    company = await session.get(Company, company_id)
    if company is None:
        message = f"No company {company_id} in this platform's registry."
        raise ValidationError(message, context={"company_id": str(company_id)})

    payload = await payload_for_job(session, job.id)
    if str(company_id) == str(payload.get("subject", "")):
        message = (
            f"{company.name} is the subject of this run. A company is not comparable "
            "with itself, and the comps table shows it on its own row already."
        )
        raise ValidationError(message, context={"company_id": str(company_id)})
    if any(peer["identifier"] == str(company_id) for peer in payload["peers"]):
        message = (
            f"This run's peer set already carries {company.name}. If the reason on it is "
            "wrong, that is the proposal to argue with rather than a second row."
        )
        raise ValidationError(
            message, context={"company_id": str(company_id), "job_id": str(job.id)}
        )

    row = OperatorPeer(
        job_id=job.id,
        company_id=company_id,
        rationale=rationale.strip(),
        added_by=actor.email,
    )
    session.add(row)
    await session.flush()

    _log.info(
        "peer.added_by_operator",
        job_id=str(job.id),
        company_id=str(company_id),
        actor=actor.email,
    )
    return row


async def addable_companies(
    session: AsyncSession, *, job_id: uuid.UUID, limit: int = ADDABLE_LIMIT
) -> list[tuple[uuid.UUID, str]]:
    """Companies this run could add as peers: held, with facts, not already on the set.

    A picker rather than a text box, because the pool is exactly the companies a comps
    table can use and offering any other would be offering the operator a refusal.
    """
    payload = await payload_for_job(session, job_id)
    as_of = _as_of_from(payload)
    taken = {peer["identifier"] for peer in payload["peers"]}
    taken.add(str(payload.get("subject", "")))

    rows = await session.scalars(
        select(Company)
        .where(
            Company.id.in_(
                select(FinancialFact.company_id).where(FinancialFact.period_end <= as_of)
            )
        )
        .order_by(Company.name)
        .limit(limit)
    )
    return [(row.id, row.name) for row in rows if str(row.id) not in taken]


async def build(
    session: AsyncSession,
    job: Job,
    *,
    subject: calc.PeerRow,
    peer_multiples: Mapping[str, tuple[calc.MultipleResult, ...]],
    basis: calc.MultipleBasis,
    as_of: date,
) -> calc.CompsTable:
    """The comps table, from a peer set a person confirmed.

    ``peer_multiples`` is keyed by peer identifier. A confirmed peer with no entry is
    **excluded with a reason** rather than dropped: "we could not compute a multiple for this
    one" is information, and a table that silently shrinks is a table whose median moved for
    reasons nobody can see.

    Peers whose reporting period sits too far from the subject's are excluded here too, by
    :func:`aer.calc.comps.align_peers`.

    Raises:
        PeerSetNotConfirmedError: If the ``PEER_SET`` gate has not approved the set.
    """
    confirmed = await confirmed_peer_set(session, job)

    # A peer recorded by name alone has no period to align: it was resolved against the
    # registry and deliberately not fetched (ADR 0059 as amended), so it is excluded with
    # that reason — the true one — rather than asked to align a date it does not have.
    dated = [peer for peer in confirmed if peer.period_end is not None]
    undated = tuple(
        calc.PeerExclusion(
            identifier=peer.identifier,
            name=peer.name,
            period_end=as_of,
            reason=UNACQUIRED_PEER_REASON,
        )
        for peer in confirmed
        if peer.period_end is None
    )

    kept, excluded = calc.align_peers(
        [
            (peer.identifier, peer.name, peer.period_end)
            for peer in dated
            if peer.period_end is not None  # the split above; restated for the type-checker
        ],
        subject_period_end=subject.period_end,
    )
    rationales = {peer.identifier: peer.rationale for peer in confirmed}

    rows: list[calc.PeerRow] = []
    missing: list[calc.PeerExclusion] = []
    for identifier, name, period_end in kept:
        computed = peer_multiples.get(identifier)
        if computed is None:
            # Distinct from the unacquired reason above: a dated peer's filings are held —
            # that is where the date came from — so what failed here is the pricing.
            missing.append(
                calc.PeerExclusion(
                    identifier=identifier,
                    name=name,
                    period_end=period_end,
                    reason=(
                        "no multiple could be computed — this platform holds the company's "
                        "filed figures but no price series for it on the as-of date"
                    ),
                )
            )
            continue
        rows.append(
            calc.PeerRow(
                identifier=identifier,
                name=name,
                period_end=period_end,
                multiples=computed,
                rationale=rationales.get(identifier, ""),
            )
        )

    table = calc.CompsTable(
        subject=subject,
        peers=tuple(rows),
        excluded=(*excluded, *missing, *undated),
        basis=basis,
        as_of=as_of,
        peer_set_confirmed=True,
        licence_note=DEFAULT_POLICIES[Provider.EODHD].licence_note,
        # Read from the policy rather than decided here. `aer.calc.comps` is pure and may
        # not consult a table, so the licence determination has to arrive as data — and
        # this is the boundary where a licence fact becomes one.
        derived_figures_publishable=DEFAULT_POLICIES[Provider.EODHD].derived_figures_publishable,
    )

    _log.info(
        "comps.built",
        job_id=str(job.id),
        peers=len(table.peers),
        excluded=len(table.excluded),
        basis=basis.value,
        as_of=as_of.isoformat(),
    )
    return table


def band_for(
    context: CalculationContext,
    *,
    key: str,
    label: str,
    basis: calc.MultipleBasis,
    observations: Sequence[tuple[date, Quantity]],
    current: Quantity | None = None,
) -> calc.MultipleBand:
    """One multiple for the subject across its own history.

    **Often the more honest comparison.** A company at 14x against peers at 11x may be
    expensive, or may be a company that has traded at 14x for a decade against peers that have
    always been cheaper for a reason the multiple does not show.

    The median and the percentile are traced, so the band's two headline figures resolve to
    recorded calculations rather than to a helper nobody can find.

    Raises:
        CalculationError: If there are no observations.
    """
    values = [quantity for _, quantity in observations]
    median = calc.median_multiple(context, observations=values)

    percentile: Decimal | None = None
    if current is not None:
        percentile = calc.percentile_rank(context, value=current, observations=values).value

    ordered = sorted(quantity.value for quantity in values)
    return calc.MultipleBand(
        key=key,
        label=label,
        basis=basis,
        observations=tuple((when, quantity.value) for when, quantity in observations),
        low=ordered[0],
        median=median.value,
        high=ordered[-1],
        current=current.value if current is not None else None,
        percentile=percentile,
    )


def specialist_multiples_for(sector_key: str) -> tuple[str, ...]:
    """The multiples this sector is properly valued on.

    Not a filter — every general multiple is still computed — but a REIT's P/FFO and a bank's
    P/TBV are the ones a reader should meet first, and a table that buried them under P/E
    would be presenting the wrong comparison prominently.
    """
    if not sector_key:
        return ()
    return tuple(
        definition.key
        for definition in calc.MULTIPLE_DEFINITIONS
        if sector_key in definition.specialist_for
    )


async def confirmed_by(session: AsyncSession, job: Job) -> str:
    """Who approved the peer set, for the table's provenance line."""
    approval = await session.scalar(
        select(Approval)
        .where(
            Approval.job_id == job.id,
            Approval.gate == GateKind.PEER_SET,
            Approval.decision == Decision.APPROVED,
        )
        .order_by(Approval.decided_at.desc())
        .limit(1)
    )
    if approval is None:
        return ""
    actor = await session.get(User, approval.actor_user_id)
    return actor.email if actor is not None else str(approval.actor_user_id)
