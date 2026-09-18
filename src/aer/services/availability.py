"""Whether this platform can research a subject at all, asked before a run starts.

**A run that cannot succeed should not start.** Until now the answer arrived at `acquire`,
which is after the planner has spent a model call and after the operator has read and
approved a plan — so a subject the platform was never going to be able to research cost real
money and real attention before saying so. The check moved to the front, where a refusal
costs one or two free requests to a register.

**It asks the question the run will ask, in the same words.** Not a heuristic about the
exchange or a list of supported companies: it resolves the subject against the register its
venue names and confirms that the register holds something this platform can read. A check
that asked a different question from the one `acquire` asks would be a check that passes on
runs that then fail, which is worse than no check.

**What "can be read" means, and why it is narrower than it sounds.** This platform reads
*tagged* filings — inline XBRL, where each figure carries its concept, its period and its
unit — because that is what lets a number be traced to a document rather than to somebody's
reading of one. EDGAR publishes every registrant's figures that way. Companies House does
too, for the companies that file through accounting software; a **listed** company's accounts
are delivered as a scanned document, and nine London-listed companies were checked on
18 September 2026 — Tesco, Barclays, AstraZeneca, Greggs, Cranswick, Chemring, Gamma, Judges
Scientific and Nichols — without finding one tagged filing among them. So a domestic London
listing is refused here, by name, with the reason. See ADR 0128.

Nothing here raises for an ordinary "no". A refusal is a sentence the operator reads, and the
caller decides what to do with it — which is the same shape `acquire_risk_free` uses, and for
the same reason: the difference between "this cannot be researched" and "something went
wrong" is a difference the operator can see and the code cannot.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

import structlog

from aer.core.enums import Provider
from aer.core.universe import registry_of
from aer.db.models import ResearchRequest
from aer.errors import AerError
from aer.sources.uk.companies_house import NOT_TAGGED_STATUS

__all__ = ["Availability", "check_availability"]

_log = structlog.get_logger("aer.services.availability")

# What a refusal says when the register holds the company but not in a form this platform can
# read. Written out here because it is the sentence an operator meets most often on a UK
# ticker, and it has to say what is wrong with the *filing* rather than with the company.
_UNTAGGED: Final = (
    "{name} files its accounts with Companies House as a scanned document rather than as "
    "tagged data. This platform reads figures from tagged filings, so that every number in a "
    "report traces to the filing that states it — and there is nothing in a scan to trace to. "
    "A company listed in London is not researchable here for that reason, whoever it is; the "
    "US listings this platform supports, and UK companies that file through accounting "
    "software, both publish tagged accounts."
)


@dataclass(frozen=True, slots=True)
class Availability:
    """Whether a subject can be researched, and the sentence to show when it cannot."""

    researchable: bool
    register: Provider

    # The register's own identifier, where the subject resolved at all. Empty otherwise, and
    # the difference matters: "no such company" and "this company files scans" are different
    # refusals and lead an operator to do different things.
    identifier: str = ""

    reason: str = ""

    def __post_init__(self) -> None:
        if self.researchable == bool(self.reason):
            message = "An availability answer is a yes with no reason, or a no with one."
            raise ValueError(message)


async def check_availability(
    request: ResearchRequest,
    *,
    sec_client: Any = None,
    companies_house_client: Any = None,
) -> Availability:
    """Can this platform research this subject? Asked before the job is created.

    Args:
        sec_client: The EDGAR client, or ``None`` on a caller that has none — in which case a
            US subject is admitted unchecked rather than refused, because a missing client is
            this machine's state and not a fact about the company.
        companies_house_client: The UK register's client, or ``None`` where no credential is
            configured. A UK subject is then refused, because without it nothing about the
            company can be established at all.

    The clients are typed loosely for the reason every other step's are: a test substitutes a
    stub without constructing one.
    """
    register = registry_of(request.exchange)
    if register is Provider.COMPANIES_HOUSE:
        return await _uk(request, companies_house_client)
    return await _us(request, sec_client)


async def _us(request: ResearchRequest, client: Any) -> Availability:
    """A US listing is researchable when EDGAR knows the ticker.

    The refusal is EDGAR's own: `resolve_ticker` already says whether the symbol is missing,
    listed on another exchange, or another company's altogether, and rewriting that here would
    be a second answer to the same question.
    """
    if client is None:
        return Availability(researchable=True, register=Provider.SEC_EDGAR)

    try:
        entity = await client.resolve_entity(request.ticker, exchange=request.exchange)
    except AerError as refused:
        return Availability(researchable=False, register=Provider.SEC_EDGAR, reason=refused.message)
    return Availability(
        researchable=True, register=Provider.SEC_EDGAR, identifier=entity.identifier
    )


async def _uk(  # noqa: PLR0911 -- one return per refusal is the readable shape
    request: ResearchRequest, client: Any
) -> Availability:
    """A UK listing is researchable when the register holds a *tagged* set of accounts.

    Three questions, in the order that makes a refusal useful: does the company exist, has it
    filed accounts this platform can fetch, and is the newest set tagged. The third is asked
    by fetching it exactly as the run would — a check that asked in some cheaper way would be
    a check that could disagree with the run it is standing in front of.
    """
    if client is None:
        return Availability(
            researchable=False,
            register=Provider.COMPANIES_HOUSE,
            reason=(
                "This platform has no Companies House credential configured, so it cannot "
                "look up a company on the UK register. A US listing is unaffected."
            ),
        )

    try:
        entity = await client.resolve_entity(
            request.ticker, exchange=request.exchange, name=request.company_name
        )
    except AerError as refused:
        return Availability(
            researchable=False, register=Provider.COMPANIES_HOUSE, reason=refused.message
        )

    try:
        history = await client.fetch_filing_history(entity.identifier)
    except AerError as unreachable:
        return Availability(
            researchable=False,
            register=Provider.COMPANIES_HOUSE,
            identifier=entity.identifier,
            reason=(
                f"The Companies House filing history for {entity.name} could not be read: "
                f"{unreachable.message}"
            ),
        )

    accounts = history.accounts()
    if not accounts:
        return Availability(
            researchable=False,
            register=Provider.COMPANIES_HOUSE,
            identifier=entity.identifier,
            reason=(
                f"{entity.name} has filed no accounts this platform can fetch. The register "
                "lists the filing history, and the older entries are index records with no "
                "document behind them."
            ),
        )

    newest = accounts[0]
    try:
        result = await client.fetch_document(newest.to_ref(company_name=entity.name))
    except AerError as unreachable:
        return Availability(
            researchable=False,
            register=Provider.COMPANIES_HOUSE,
            identifier=entity.identifier,
            reason=(
                f"The accounts {entity.name} filed on {newest.filed_on.strftime('%-d %B %Y')} "
                f"could not be fetched: {unreachable.message}"
            ),
        )

    if result.status_code == NOT_TAGGED_STATUS:
        _log.info(
            "availability.untagged",
            company_number=entity.identifier,
            filed_on=newest.filed_on.isoformat(),
        )
        return Availability(
            researchable=False,
            register=Provider.COMPANIES_HOUSE,
            identifier=entity.identifier,
            reason=_UNTAGGED.format(name=entity.name),
        )

    if not result.ok:
        return Availability(
            researchable=False,
            register=Provider.COMPANIES_HOUSE,
            identifier=entity.identifier,
            reason=(
                f"Companies House did not serve {entity.name}'s newest accounts, so this run "
                "would have nothing to read. Trying again later is worth doing: this is the "
                "register having a bad moment rather than a fact about the company."
            ),
        )

    return Availability(
        researchable=True, register=Provider.COMPANIES_HOUSE, identifier=entity.identifier
    )
