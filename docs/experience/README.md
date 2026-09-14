# The experience design

*The customer experience of the whole system: every tool defined, what success means for each,
and the screens that carry them. Begun 14 September 2026, after the
[end-state plan](../plan/end-state-2026-09.md) settled what the system becomes.*

This folder exists because the platform stopped being a report generator and became a loop —
**research, decide, hold, review** — and an interface organised around nine tools cannot carry
a journey organised around four stages.

## Authority

1. [`../plan/ROADMAP.md`](../plan/ROADMAP.md) and the ADRs still outrank everything here.
2. [`../design/`](../design/README.md) remains the specification the **shipped** interface is
   answerable to, and stays the record of what runs today. Nothing in this folder is built yet.
3. This folder supersedes `../design/00-the-product.md` and
   `../design/02-information-architecture.md` **for the end-state system only**, and says so
   where it does.

## Read in this order

| Document | What it settles |
|---|---|
| [`00-the-tools.md`](00-the-tools.md) | Every tool and sub-tool, what each must make possible, and the measurable bar for each |
| `01-information-architecture.md` | How the fourteen surfaces are grouped, named and reached *(next)* |
| `02-screens/` | The drawn designs, screen by screen *(next)* |

## The two ideas everything else follows from

**The home page is an action queue, not a dashboard.** A dashboard tells you numbers you did
not ask for. A queue tells you the four things that need you today — a gate waiting, a premise
that broke, a report gone stale, a position with no thesis — and nothing else.

**The company is the object, not the tool.** A user thinks *"what do I know about Microsoft?"*,
never *"which of my nine tools holds the Microsoft thing?"* One page per company gathers the
report, the thesis, the position, the decisions and the monitor's verdict, and every tool is
reached *through* the company rather than beside it.
