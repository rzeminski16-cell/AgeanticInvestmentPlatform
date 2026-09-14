# The main menu

**`/`** — the front door, and the one page that has to render when nothing else can.

---

## At a glance

| | |
|---|---|
| **URL** | `/` (and `/overview`, a permanent redirect — the page moved and the URL was bookmarked) |
| **Who arrives** | The operator, opening the application. Every session starts here |
| **From where** | A bookmark, the product name in the header, or the Overview nav item |
| **What they came for** | One of two things, and the page cannot know which: *what can this thing do* on the first visit, and *what is waiting for me* on every visit after |
| **Current templates** | `index.html`, `overview/_attention.html`, `overview/_missing.html` |
| **Token state** | **Clean.** Zero raw ramp classes. This page is the reference, not the work |

---

## The job

**Answer "is anything waiting for me?" without being asked, and show the shape of the whole
product to somebody who has never seen it.**

Those are two different jobs for two different visitors, and the page currently does them in
that order, launcher first. Whether that order is right is the first design question below.

---

## What is on it

Four blocks, in this order today.

### 1. The launcher — nine tools

From the tool registry. Each row carries:

| Field | Example | Notes |
|---|---|---|
| `label` | "Equity Research" | |
| `status` | `Working` · `Under construction` · `Planned` | **Written, not only coloured** |
| `href` | `/requests` | Where the tool lives. Working tools go somewhere real |
| `summary` | One or two sentences | What the tool is for, in the operator's language |
| `needs` | "A standing budget that is not one run's cap…" | **Only on unbuilt tools.** What has to exist before it can |
| `action_label` / `action_href` | "Start a research request" → `/requests/new` | **Only on working tools.** A tool that is not built may not carry an action — it raises at import rather than shipping a button that goes nowhere |

Two of the nine are working. Seven are planned.

**The `needs` field is the one that stops a placeholder being a progress bar.** Somebody
opening a planned tool is asking why it is not here, and *"attestations and their two grades:
a fill price is not filed, not chosen and not calculated"* is an answer. Design it as content,
not as a subtitle to be truncated.

### 2. The counts — "Where you left off"

A small number of key–value tiles. Today: one registered badge (`Waiting for you`, counting
runs stopped at a gate) plus spend this month with the period as a note ("Since 1 August").

Counts come from the same registry the navigation badge uses, so a tool contributes its count
once and it appears in both places. **Expect more of these as tools ship** — the design should
hold three to eight tiles without rearranging.

**Money is rendered in Python, always.** A total that rounds to nothing says `under £0.01`
rather than `£0.00`, because "we have spent nothing" and "we have spent a third of a penny"
are different answers and only one is true.

### 3. The work list — "Your attention"

The most valuable block on the page. Grouped by severity, never sorted into one list, because
*"three runs are waiting for you"* and *"one run failed"* are different kinds of day.

| Severity | Meaning | Order |
|---|---|---|
| **Blocked** | Work that has stopped and will not restart without a person | First — it resumes the moment you decide |
| **Broken** | Work that went wrong and needs diagnosis | Second |
| **Idle** | Work nobody started | Last |

Each item carries:

| Field | Example |
|---|---|
| `title` | "Contoso plc is waiting for you" |
| `detail` | "The run stopped so you could confirm its peer set." |
| `href` + `action` | `/runs/{id}` + "Open the run" |
| `preview_href` | `/research/runs/{id}/preview` — opens the drawer. Absent on items with nothing to preview |
| `tool` | Which tool it belongs to |

**A gate is named, not counted.** "One run is waiting" sends an operator to a console to find
out what it wants; "Contoso is waiting for you to confirm its peer set" is the same row doing
the work. That costs two database queries per stopped run and it is the best two queries on
the page. **Preserve this.** Every gate has a phrase written for it in the second person.

The list is **bounded** — at most eight of each kind — and when it is cut short it says so
with a row of its own ("6 more runs are waiting at a gate"), rather than showing eight and
implying that is all.

**A provider that fails becomes an item saying so.** This is the difference between the work
list and the counts: losing a count costs a number nobody was relying on, but this feed is the
answer to "is anything waiting for me", and a silent failure would answer "no" — the one
answer that must never be guessed.

### 4. The build identity

A single line of small text at the bottom. Which commit is running. Unglamorous and
load-bearing: more than one reported defect has been a checkout three commits behind.

---

## Inputs

**None.** This page collects nothing. Every control on it is a link.

That is worth stating because it constrains the design usefully: there is no form to lay out,
no validation to show, and no reason for anything here to be anything other than immediately
actionable.

---

## States

### The ordinary state
Launcher, counts, work list with items. Nine tools, two of them live.

### Nothing waiting
The work list is replaced by a single empty-state block. The current copy is the model for
the whole product:

> **Nothing is waiting** — No run is stopped at a gate, nothing failed, and every request you
> have written has been run. Start another when you are ready. *[Commission research]*

It names what was checked, so the reader can trust the answer, and it offers the next action.
**Never "no results".**

### First run — nothing exists at all
No requests, no runs, no portfolio, no spend. Today this is the same as "nothing waiting", and
it should not be: a first-time visitor and a caught-up veteran are being told the same thing
by the same words, and only one of them knows what to do next. **This is a real gap — see
below.**

### The database is down
**The most important state on the page, and the reason this page is built the way it is.**

The launcher renders from the registry and needs no database. Everything below it does. So
when Postgres is unreachable, the counts and the work list are replaced by a notice saying
*which* failure it is, and the tools still render.

Two distinguishable failures, with completely different fixes, and reporting the second as the
first sends the operator to restart a container that was working perfectly:

- **Not reachable** — "The database is not reachable. Start it with `just up`, then reload
  this page. `/readyz` reports which dependencies are answering."
- **Reachable but behind the models** — the schema drift message, naming the objects rather
  than the count, so the operator knows which migration they skipped.

The notice currently sits *below* the launcher, and adds: "The tools above are listed from the
registry, so they render either way."

**Design constraint:** this state must be reachable in your design and must not depend on any
data. Every other page in the product fails loudly without a database — degrading a page that
shows data would mean showing an empty list as though it were the truth. This one page
degrades instead, and the reason is that the most likely explanation for somebody opening it
is that something is broken.

### Guidance mode on
Numbered callout chips appear beside three blocks, explaining what each is. Off by default,
toggled from the menu. See [`shell-and-menu.md`](shell-and-menu.md).

---

## What is wrong today

**The two jobs are in the wrong order for the common case.** The launcher is first and the
work list is last, so the returning operator — every visit after the first — scrolls past nine
tool cards, seven of which they cannot use, to reach the three rows they came for. The reason
given for the order is that the first question on arrival is "what can this do"; that is true
exactly once.

**Nothing conveys time or money spent.** An item that has been blocked for six days looks
identical to one blocked for six minutes. A run that has already consumed £7 of its £8 ceiling
looks identical to one that has spent nothing. Both facts are recorded and neither reaches
this page, and both change what the operator should do next.

**The counts and the work list overlap without acknowledging it.** "Waiting for you: 3" sits
directly above three rows that are the same three runs. One is a number and the other is the
answer; showing both without relating them costs a block of vertical space to say the same
thing twice.

**The first-run state does not exist.** A brand-new installation shows "Nothing is waiting",
which is true and useless. The one moment the platform has the reader's complete attention is
spent telling them nothing is wrong.

**Seven placeholder cards is a lot of the page.** They are honest and they are worth keeping,
but at the moment they occupy roughly the same visual weight as the two working tools, on the
page where the operator most needs the two working tools.

---

## What to improve

**1. Decide what this page is for, and order it accordingly.** The likeliest answer: the work
list leads, the launcher becomes something more compact below it, and the first-visit case is
handled by the empty state rather than by the page order. But the launcher genuinely is how
somebody understands the product, so if you demote it, replace what it was doing.

**2. Give the work list time and cost.** How long has this been waiting? What has it already
consumed, against what ceiling? Is it close to done? A run stopped one step from the end after
£8 and one stopped at the first gate after £0.15 want completely different decisions, and the
page currently presents them identically. *(Both figures exist. Neither is on the page.)*

**3. Distinguish the working tools from the planned ones more sharply.** Two tools an operator
can use and seven they cannot are not the same kind of thing and should not be the same kind
of card. The status chip does the semantic work; the layout does not yet.

**4. Design the first-run state properly.** A new installation should say what to do first,
and the honest answer is short: configure a model provider, then commission a report. This is
the platform's only onboarding surface and it does not exist.

**5. Resolve the counts-versus-list duplication.** Either the counts summarise things the list
does not show, or they go.

**6. Consider what the page looks like with six tools working.** The launcher is designed to
grow and the work list will grow with it — several tools contributing items into one feed is
the case the whole registry mechanism exists for, and it has never been seen because only one
tool contributes today.

---

## What must not change

**The launcher must render without a database.** It is the whole design of this page. If your
design makes the tool list depend on data, the one page that works when the platform is broken
stops working when the platform is broken.

**A planned tool says what it needs.** Not "coming soon". Not a progress bar. The specific
prerequisite, in the operator's language.

**The work list is grouped by severity, not sorted into one stream.** And blocked comes before
broken, because blocked work resumes the moment somebody decides and broken work needs
reading first.

**A bounded list says it is bounded.** Showing the first eight of fourteen without saying so
describes a smaller problem than the operator has.

**A failed provider appears as an item.** Never silence.

---

## Done when

- A returning operator can answer "is anything waiting, and is any of it urgent?" without
  scrolling.
- A first-time visitor is told what to do first, in one sentence, on the page.
- Every attention row conveys *what*, *why*, *how long*, and *what it has cost* — and offers
  both a preview and a destination.
- The tool launcher still communicates the nine-tool shape, and still renders with the
  database stopped.
- The page holds eight attention rows and eight count tiles without becoming a scroll.
