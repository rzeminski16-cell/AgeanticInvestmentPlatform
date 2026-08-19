"""The theme-proposal role: recurring subjects named with reasons, nothing else.

`docs/knowledge-graph.md` K1, ADR 0065. The comparable relation can say "a run of A named
B as a peer"; it cannot say *AI capital expenditure* links a hyperscaler, a fab and a
utility. Naming that kind of connection is a judgement about the market — the model's half
of the division of labour — and it enters the graph exactly the way a peer set does:
proposed here, confirmed by a person at the ``THEME_SET`` gate, an edge only after that.

**The confinement is the schema; the deduplication is code's.** :class:`ThemeSlate` has
one field: a bounded list of key, label and rationale. There is nowhere to put a figure, a
rating or a company list — a theme's membership is the *subject* company, decided by the
run it was proposed in, never by this role. And a key is a claim about identity, not an
identity: the service slugs it and matches it against the ``themes`` table in code, so
"AI Capex" and "ai-capex" cannot found two themes however the model spells them. The
existing keys are shown to the model as vocabulary to reuse; whether it does is checked,
not trusted.

The role holds no tools and answers from the subject's identity and classification alone.
It runs before the research workers, so there are no findings to hand it — and that is
fine, because "what larger story is this company part of" is a question about the market,
not about this run's evidence.
"""

from __future__ import annotations

from typing import ClassVar, Final

from pydantic import BaseModel, ConfigDict, Field

from aer.agents.base import Agent

__all__ = [
    "PROPOSED_BY",
    "THEME_SLATE_LIMIT",
    "ProposedTheme",
    "ThemeProposalAgent",
    "ThemeProposalInput",
    "ThemeSlate",
]

PROPOSED_BY: Final = "aer.agents.themes"

# The most themes one run may propose. A company genuinely sits in two or three stories;
# a slate of ten is a tag cloud, and a reviewer confirming a tag cloud is a reviewer
# clicking through — the failure the gate exists to prevent. The same reasoning as the
# peer slate's bound, with a smaller number because themes are coarser than peers.
THEME_SLATE_LIMIT: Final = 5

_KEY_CEILING: Final = 64
_LABEL_CEILING: Final = 120
_RATIONALE_CEILING: Final = 400
_RATIONALE_BUDGET: Final = 250


class ProposedTheme(BaseModel):
    """One proposed theme: a key to match, a label to display, a reason to review.

    The key is what code acts on — it is slugged and matched against the existing themes
    exactly, so a proposal that reuses a listed key joins that theme and anything else
    founds a new one *pending confirmation*. The label is only read when the key is new;
    an existing theme keeps the label it was founded under.
    """

    model_config = ConfigDict(extra="forbid")

    key: str = Field(
        min_length=1,
        max_length=_KEY_CEILING,
        description="Short kebab-case identity, e.g. 'ai-capex'. Reuse a listed key exactly.",
    )
    label: str = Field(
        min_length=1,
        max_length=_LABEL_CEILING,
        description="The display name, e.g. 'AI capital expenditure'.",
    )
    rationale: str = Field(
        min_length=1,
        max_length=_RATIONALE_CEILING,
        description="Why the subject belongs to this theme.",
    )


class ThemeSlate(BaseModel):
    """The whole of what this role may return: a bounded list of proposals.

    One field and no others — the ADR 0059 confinement pattern. An empty list is a valid
    answer for a company that is not part of any larger story worth tracking, and is
    better than a stretched one: a theme with one member forever is noise the statistics
    page counts against the extraction.
    """

    model_config = ConfigDict(extra="forbid")

    themes: list[ProposedTheme] = Field(
        default_factory=list,
        max_length=THEME_SLATE_LIMIT,
        description="Proposed themes, strongest first.",
    )


class ThemeProposalInput(BaseModel):
    """What the role is given. No tools, so this is the whole of what it can see."""

    model_config = ConfigDict(extra="forbid")

    company_name: str
    ticker: str
    exchange: str
    as_of_date: str

    # The filer's own classification, when the run resolved one; empty strings otherwise,
    # said plainly in the prompt so the model does not guess at what it was not shown.
    sic: str = ""
    sic_description: str = ""
    sector: str = ""

    # The vocabulary that already exists, as "key — label" lines. Data from this
    # platform's own confirmed rows, not fetched content; shown so the model reuses an
    # existing identity instead of founding a near-duplicate the code cannot merge.
    existing: list[str] = Field(default_factory=list)


class ThemeProposalAgent(Agent[ThemeProposalInput, ThemeSlate]):
    """Proposes themes for one subject. Nothing else."""

    role: ClassVar[str] = "theme_proposal"
    output_schema: ClassVar[type[BaseModel]] = ThemeSlate

    # Tools and token caps live in this role's `aer.agents.registry` definition.

    prompt_version: ClassVar[str] = "1"

    def system_prompt(self, payload: ThemeProposalInput) -> str:  # noqa: ARG002
        return _SYSTEM_PROMPT

    def user_message(self, payload: ThemeProposalInput) -> str:
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
        if payload.existing:
            lines.append("")
            lines.append("Themes this research library already tracks (reuse a key exactly):")
            lines.extend(f"  - {item}" for item in payload.existing)
        else:
            lines.append("")
            lines.append("This research library tracks no themes yet.")
        return "\n".join(lines)


_SYSTEM_PROMPT = f"""\
You propose investment themes for one subject company: the recurring, cross-company
subjects a research library files it under — the kind of connection that links a
hyperscaler, a chip fab and a utility through one force acting on all three. Your whole
output is one JSON object with a single `themes` list, and there is no field in it for
anything else.

Rules.

1. At most {THEME_SLATE_LIMIT} themes, strongest first. Each entry carries a `key` of at \
most {_KEY_CEILING} characters in kebab-case, a `label` of at most {_LABEL_CEILING}, and \
a `rationale` of at most {_RATIONALE_CEILING}. These bounds are stated here because the \
schema's own bounds reach you as description text rather than as a rule the server \
applies — a reply that overruns them is thrown away after it has been paid for.

2. When a listed existing theme fits, reuse its key **exactly as listed**. The library's \
value is one theme spanning many companies; a near-duplicate key founds a rival spelling \
that nothing can merge. Propose a new key only for a genuinely untracked subject.

3. A theme is a durable economic or structural force, not a description of the company. \
"Cloud infrastructure spend", "GLP-1 demand", "grid electrification" are themes; \
"large software company" is a classification and "strong quarter" is news. If the \
subject's industry classification already says it, it is not a theme.

4. The rationale is what the reviewer decides on. Keep it to roughly \
{_RATIONALE_BUDGET} characters and make it specific: how this force reaches this \
company's economics, and roughly how exposed it is. "Operates in this space" is a \
classification, not a rationale.

5. You never produce a figure, a rating or a company list. The theme's membership is the \
subject company alone — other companies join a theme through their own runs, each behind \
its own gate.

6. Fewer is fine, and none is a valid answer. An empty list is honest; a stretched one \
files the company under stories it is not actually part of, which misleads every later \
reader of the library.

7. You are proposing. A person confirms the slate at a gate before any of it becomes \
part of the research library, and an unconfirmed theme contributes nothing.
"""
