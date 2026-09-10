# ADR 0112 — The menu is grouped by what you are doing, not by which tool does it

**Status.** Accepted
**Date.** 2026-09-09
**Amends.** ADR 0071 (a tool is a registered capability), whose registration contract is
unchanged: a tool still contributes one `NavSection` and one line. What changes is that the
section stops being a *heading*.
**Required by.** The first full manual acceptance pass
(`docs/plan/acceptance-2026-09-08.md` §11): *"the side menu is getting very cluttered,
consider grouping the tools together."*

## Context

The sidebar was ten headings over eighteen destinations. **Seven of those headings stood
over a single link, and six of the seven said the same word as the link beneath them** —
Watchlist over Watchlist, Portfolio over Portfolio, Risk over Risk, Theses over Theses,
Decisions over Decisions, Monitor over Monitor. The chrome at the width where the rail
collapses said it twice in one line: *"Watchlist · Watchlist"*.

That was not a design anybody chose. It followed from the shape ADR 0071 settled, which is
a good shape: a tool is a registered capability, it contributes a `NavSection` from its own
module, and `shell/registry.py` imports it and adds one line. `NavSection` carried a
`label`, the template drew one heading per section, and so **one heading per tool became a
rule nobody decided**. It reads as organisation at the second tool and as a wall at the
ninth, and it gets worse with every tool that ships — which is the tell that the defect is
structural rather than cosmetic.

The measurement matters because it names what is wrong. Eighteen destinations is not many.
Ten headings for eighteen destinations is a table of contents nearly as long as the
contents, and a heading that repeats its only child is a line of the menu that carries no
information at all.

## Decision

**A tool contributes destinations. The shell decides the headings.**

`NavGroup` is a heading over the sections of however many tools sit beneath it, declared in
`GROUPS` in `shell/registry.py` and nowhere else. `NavSection` **loses its `label`**, which
is the substance of this ADR rather than a tidy-up: a field that only ever became a heading
is the mechanism by which every tool got one, and leaving it in place would invite the next
person to draw it again.

Four headings, grouped by what the operator is doing:

| Heading | Destinations |
|---|---|
| *(none)* | Overview |
| Research | Requests, Active run, Reports, Skills, Knowledge, Watchlist |
| Your book | Portfolio, Risk, Decisions, Post-trade review, Decision analytics |
| What you believe | Theses, Monitor |
| Platform | Settings, Costs, Health, API |

**A group with no label draws no heading.** The home page needs it: a category of one, named
after the link inside it, is the whole problem in miniature. It is one concept rather than a
special case — "ungrouped" is a group whose name is nothing.

**The division of labour is enforced by the type, not by discipline.** `NavSection` has no
field a tool could use to ask for a heading or to name a group, so a tenth tool adds a line
inside an existing group and cannot add a tenth heading by existing. That is also the point
of putting the line *inside* a group: whoever ships the next tool has to decide where their
work sits in somebody's day, and the type gives them no way to avoid the question.

### The names are the arguable part, and they are cheap to change

**"Your book" and "What you believe" are a guess at how the operator thinks about the
split.** They are four string literals in one tuple. No tool learns a word, no route moves,
no test asserts a heading's prose beyond the list itself, and nothing outside
`shell/registry.py` needs editing to reword the entire menu.

The split they encode is a claim worth stating so it can be disagreed with: **what you own
is not the same as what you think.** A thesis is a position you have written down and the
monitor is the world contradicting one; neither belongs beside the ledger of what you hold,
what it exposes you to, what you decided and how the decisions turned out.

The table above already carries one such disagreement, and that is the mechanism working
rather than a correction to it. Watchlist was drafted under *What you believe* — a company
you have an opinion about and no position in — and the operator moved it to *Research*, on
the ground that a watchlist commissions research runs (ADR 0107) and what comes out is a
request like any other. That is the better reading, and it cost one line.

## Consequences

**The chrome stops repeating itself.** `Shell.location` — what a reader sees at the width
where the rail collapses, and the only thing on screen saying where they are — was
`"{section.label} · {item.label}"` and is now the group's word. *"Watchlist · Watchlist"*
becomes *"Research · Watchlist"*; a page under no heading is simply named.

**Three registries now ask a slightly different question.** The badge registry and the
attention registry each refuse a provider owned by a tool the navigation has never heard of,
which used to be `{section.tool for section in NAV}`. `flat_sections()` is that answer now:
the tools, in the order the groups place them. The check is unchanged; only the walk is
longer by one level.

**The guards are measurements, not taste.** Four of them, each one of the numbers above
refusing to come back: no heading over a single destination; no heading repeating a link
beneath it; every registered section in exactly one group, walked from the registry's own
module so that a section imported and never placed fails rather than disappearing; and
`NavSection` having no field a tool could use to ask for a heading.

## Alternatives considered

**Show a section's label only where the section has more than one item.** This keeps the
per-tool heading and hides the worst of it, which is the trouble: it fixes the six visible
repetitions and leaves the rule that produced them, so the wall comes back at the twelfth
tool with headings that happen to have two children each. It also makes the menu's shape
depend on how many pages a tool happens to have, which is not a fact about the operator.

**Let each tool declare its own group.** Then the grouping is nine opinions rather than one,
and the first tool to disagree gets a tenth heading — the same failure, arrived at by a
longer road. Where a tool sits in a menu is a judgement about the whole product, and no
single contributor is in a position to make it.

**Collapsible groups.** More machinery, a per-viewer state to store, and it answers a
different problem: a long menu you can fold. Eighteen destinations is not long. Ten headings
was the problem, and folding them would have preserved all ten.

**Drop `NavSection` and let groups hold items directly.** Tempting, and wrong: the section is
the unit of *contribution* and it is what `tool` hangs off, which is what the badge and
attention registries reconcile against and what ADR 0071's one-import-per-tool contract
means. Presentation stopped using it; ownership never did.
