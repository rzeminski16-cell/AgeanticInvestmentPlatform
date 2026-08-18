"""The peer-proposal role: comparable companies named by ticker, with reasons.

ADR 0059. `aer.services.comps.propose_peers_from_sic` proposes only companies this
database already holds, so a fresh database proposes nobody and the comps table has never
been reachable on a first run. Naming plausible comparables for a listed company is a
judgement with a rationale — the model's half of the division of labour — and the peer-set
gate already exists to put exactly that judgement in front of a person.

**The confinement is the schema and the registry, and the containment is downstream.**
:class:`PeerSlate` has one field: a bounded list of ticker, name and rationale. There is
nowhere to put a figure, a rating, or an approval. And a ticker is a *claim*, not a peer:
`aer.services.peer_discovery` resolves every one against EDGAR's own registry, refuses the
unresolvable by name, refuses the subject as its own peer, and only then fetches anything.
A hallucinated company costs one lookup and appears in the step's refusals, never at the
gate.

The role holds no tools and answers from its own knowledge of the market. That is the
point rather than a compromise: what the platform lacks on a first run is precisely
knowledge it has not acquired yet, and everything the model asserts here is either
verified in code (the ticker exists) or reviewed by a person (the rationale holds).
"""

from __future__ import annotations

from typing import ClassVar, Final

from pydantic import BaseModel, ConfigDict, Field

from aer.agents.base import Agent

__all__ = [
    "PEER_SLATE_LIMIT",
    "PROPOSED_BY",
    "PeerProposalAgent",
    "PeerProposalInput",
    "PeerSlate",
    "ProposedPeer",
]

PROPOSED_BY: Final = "aer.agents.peers"

# The most peers the slate may carry. The same figure as `aer.services.comps`'s
# MAX_PROPOSED_PEERS, for the same reason — a reviewer confirming twenty companies is a
# reviewer clicking through — and a test pins the two to each other. Written out here
# rather than imported because the agents package sits underneath the services that
# construct agents, and reaching up would cycle.
PEER_SLATE_LIMIT: Final = 8

_TICKER_CEILING: Final = 12
_NAME_CEILING: Final = 120
_RATIONALE_CEILING: Final = 400
_RATIONALE_BUDGET: Final = 250


class ProposedPeer(BaseModel):
    """One proposed comparable: a ticker to verify, a name to display, a reason to review.

    The ticker is what code acts on — resolution against the registry keys on it. The name
    is the model's and is *replaced* by the registry's on resolution, so a right ticker
    with a wrong name self-corrects and a right name with a wrong ticker is refused.
    """

    model_config = ConfigDict(extra="forbid")

    ticker: str = Field(
        min_length=1,
        max_length=_TICKER_CEILING,
        description="The exchange ticker, as EDGAR lists it.",
    )
    name: str = Field(
        min_length=1,
        max_length=_NAME_CEILING,
        description="The company's name, for the reviewer.",
    )
    rationale: str = Field(
        min_length=1,
        max_length=_RATIONALE_CEILING,
        description="Why this company is comparable to the subject.",
    )


class PeerSlate(BaseModel):
    """The whole of what this role may return: a bounded list of proposals.

    One field and no others — ADR 0059's confinement is this class, exactly as ADR 0046's
    is `AssumptionProposalDraft`. An empty list is a valid answer for a company with no
    good comparables, and is better than a stretched one.
    """

    model_config = ConfigDict(extra="forbid")

    peers: list[ProposedPeer] = Field(
        default_factory=list,
        max_length=PEER_SLATE_LIMIT,
        description="Proposed comparables, best first.",
    )


class PeerProposalInput(BaseModel):
    """What the role is given. No tools, so this is the whole of what it can see.

    Identity and classification only. The step runs before the research workers, so there
    are no findings to hand it — and that is fine, because comparability is a question
    about the market rather than about this run's evidence.
    """

    model_config = ConfigDict(extra="forbid")

    company_name: str
    ticker: str
    exchange: str
    as_of_date: str

    # The filer's own industry classification, when the run resolved one. Empty strings
    # otherwise — said plainly in the prompt rather than omitted, so the model does not
    # guess at what it was not shown.
    sic: str = ""
    sic_description: str = ""

    # Which sector model applies, when a specialist classification was proposed.
    sector: str = ""


class PeerProposalAgent(Agent[PeerProposalInput, PeerSlate]):
    """Proposes comparable companies by ticker. Nothing else."""

    role: ClassVar[str] = "peer_proposal"
    output_schema: ClassVar[type[BaseModel]] = PeerSlate

    # Tools and token caps live in this role's `aer.agents.registry` definition. A
    # declaration here would grant nothing, and would be a second place to read.

    prompt_version: ClassVar[str] = "1"

    # Routed under its own name — see `aer.config.DEFAULT_MODEL_ROUTES`. Borrowing another
    # role's route (the section writer's gap-O1 arrangement) is for a *definition row* that
    # wants a cheaper model for the same capability; a genuinely new role that borrowed one
    # would be unroutable under its own name, and `test_agent_registry` says so.

    def system_prompt(self, payload: PeerProposalInput) -> str:  # noqa: ARG002
        return _SYSTEM_PROMPT

    def user_message(self, payload: PeerProposalInput) -> str:
        lines = [
            f"Subject company: {payload.company_name} ({payload.ticker}, {payload.exchange})",
            f"As-of date: {payload.as_of_date}",
        ]
        if payload.sic:
            described = (
                f"{payload.sic} — {payload.sic_description}"
                if payload.sic_description
                else payload.sic
            )
            lines.append(f"Filer's SIC classification: {described}")
        else:
            lines.append("Filer's SIC classification: not resolved by this run.")
        if payload.sector:
            lines.append(f"Sector model: {payload.sector}")
        return "\n".join(lines)


_SYSTEM_PROMPT = f"""\
You propose comparable companies for one subject: the peer set an equity research
comparables table would be built from. Your whole output is one JSON object with a single
`peers` list, and there is no field in it for anything else.

Rules.

1. At most {PEER_SLATE_LIMIT} peers, best first. Each entry carries a `ticker` of at most \
{_TICKER_CEILING} characters, a `name` of at most {_NAME_CEILING}, and a `rationale` of at \
most {_RATIONALE_CEILING}. These bounds are asked for here because the schema's own bounds \
reach you as description text rather than as a rule the server applies — a reply that \
overruns them is thrown away after it has been paid for.

2. Tickers must be US listings as the SEC's EDGAR registry knows them, because every one \
you propose is resolved against that registry in code. A ticker EDGAR does not know is \
refused by name and shown to the reviewer as a refusal, so a guess helps nobody. Do not \
propose the subject itself, a subsidiary of it, or a different share class of it.

3. Comparable means: an investor deciding what the subject is worth would look at what \
the market pays for this company. Similar business model, similar economics, overlapping \
end markets. A giant and a minnow in the same industry can still be comparable; a \
conglomerate that happens to contain a similar division usually is not — say so in the \
rationale when the fit is partial.

4. The rationale is what the reviewer decides on. Keep it to roughly \
{_RATIONALE_BUDGET} characters and make it specific to the pair: what overlaps, what does \
not, and why the multiple is still informative. "Same industry" is a classification, not \
a rationale.

5. You never produce a figure. No market capitalisations, no multiples, no growth rates — \
the platform computes every number from acquired filings and prices, and a figure of \
yours has no source it can trace.

6. Fewer is fine, and none is a valid answer for a company with no good comparables. An \
empty list is honest; a stretched one puts a misleading median in front of a person.

7. You are proposing. Every ticker is verified against the registry, the companies' \
filings are acquired and parsed by code, and a person confirms the set at a gate before \
any comparison is computed.
"""
