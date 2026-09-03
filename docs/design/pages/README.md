# The page specifications

One document per surface. Each is self-contained and written to be handed to a designer — or
pasted into an AI design tool — on its own, alongside
[`../01-constraints.md`](../01-constraints.md) and
[`../03-design-system.md`](../03-design-system.md).

| Document | Surfaces |
|---|---|
| [`overview.md`](overview.md) | The main menu: launcher, counts, work list, the degraded state |
| [`shell-and-menu.md`](shell-and-menu.md) | Header, menu panel, badges, drawer, footer, preferences |
| [`research-requests.md`](research-requests.md) | Requests list, the commission form, request detail, removal |
| [`research-run-console.md`](research-run-console.md) | The run console — the hub of the research tool |
| [`research-gates.md`](research-gates.md) | All seven gates, and the assumptions surface |
| [`research-evidence.md`](research-evidence.md) | Sources, claims, footnotes, valuation, calculations, replay |
| [`research-reports.md`](research-reports.md) | Report history, a finished report, its HTML notation |
| [`research-skills-and-knowledge.md`](research-skills-and-knowledge.md) | The skills library and editor; the knowledge graph and company history |
| [`portfolio.md`](portfolio.md) | The book, its empty and broken states, the transaction form |
| [`theses.md`](theses.md) | The theses list, one thesis and its premises, the four forms |
| [`monitor.md`](monitor.md) | The findings list, one finding, the thesis gate and the acts that close a finding |
| [`decisions.md`](decisions.md) | The journal, one decision with the premises it was taken on and the trades that carried it out |
| [`review-and-analytics.md`](review-and-analytics.md) | The closed positions, the reviewer's proposal beside the outcome, the confirmed review, and the analytics with an `n` on every statistic |

---

## How each document is structured

The same nine headings every time, so a designer can find the same thing in the same place.

**At a glance** — the URL, who arrives, from where, and what they came for.

**The job** — one sentence. What this surface must make possible. If a design does this
well and everything else badly, it is still a better design.

**What is on it** — the data contract. Every field the page has available, where it comes
from, and how it is rendered. This is the part that stops a design inventing data that does
not exist or omitting data that must appear.

**Inputs** — every control, what it accepts, where its validation lives, and how a rejection
reaches the reader. Where a surface has no inputs, it says so.

**States** — every state the surface can be in. This section is long on purpose: a
specification that covers only the happy path is a specification that will be finished by
whoever implements it, at the worst possible moment.

**What is wrong today** — specific and honest. Not a wish list.

**What to improve** — the design questions, roughly in order of value. **These are questions,
not instructions.** Where there is an obvious answer it says so; where the trade is genuine
it says that too.

**What must not change** — the non-negotiables for this surface, distinct from the global
constraints. Usually two or three things, each with the failure it prevents.

**Done when** — how to tell the design worked. Written so it can be checked against a mockup
rather than only against a build.

---

## A note on the "what is wrong today" sections

They are blunt. That is not a criticism of the people who built these screens — almost every
one of them is correct, well-reasoned, and documented with more care than most production
code receives. The problem is not that any single page is bad.

The problem is that **each page was designed in isolation and they were never designed
together.** A run console that is excellent at showing a run, next to a gate that is
excellent at showing a gate, produces a tool where the operator has to rebuild the context
themselves at every step. That is the thing to fix, and it is not fixable one page at a time
— which is why this is a specification for all of them at once rather than a list of tickets.
