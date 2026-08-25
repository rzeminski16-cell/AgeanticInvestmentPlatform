# Information architecture

*Every surface in scope, how they connect, and the four journeys that matter.*

---

## The shape today

```
/                          The main menu — launcher + work list
│
├── Overview
│     └── /                (the same page; it is the front door and a nav item)
│
├── Research
│     ├── /requests        list → new → detail → edit / remove / assumptions
│     │     └── /runs/{id} the console → 7 gates → evidence surfaces
│     ├── /reports         history → a finished report → its HTML notation
│     ├── /skills          library → editor → import diff → dry run
│     └── /knowledge       measurements → the graph → a company's history
│
├── Portfolio
│     └── /portfolio       the book as at a date  (+ empty, + broken)
│
└── Platform               (in the menu; not being redesigned)
      ├── /settings  ├── /costs  ├── /healthz  └── /docs
```

**Two structural facts you cannot see from the tree.**

The **launcher** on `/` lists all nine tools, including the seven that do not exist. The
**navigation** lists only the two that do. That split is deliberate: a menu offering seven
things nobody can use is worse than a front page that shows the shape of the product once and
gets out of the way.

The **run console is the hub of the research tool**, not the request. A request is a
commission; the run is the thing with state, and every gate and every evidence surface hangs
off `/runs/{id}`.

---

## The complete surface inventory

Everything an operator can open. `†` marks a fragment rather than a page — it is fetched into
an existing page and has no navigation of its own.

### Overview and shell

| URL | What it is | Spec |
|---|---|---|
| `/` | The main menu: launcher, counts, work list | [`pages/overview.md`](pages/overview.md) |
| `/overview` | Permanent redirect to `/` — the page moved, the URL was bookmarked | " |
| `/_shell/badges` † | The nav's counts, fetched after the page renders | [`pages/shell-and-menu.md`](pages/shell-and-menu.md) |
| `/research/runs/{id}/preview` † | A run, close enough to triage, in the drawer | " |
| `POST /_shell/theme` | Light / dark / system | " |
| `POST /_shell/guidance` | Show or hide the explanatory callouts | " |

### Research — the request

| URL | What it is | Spec |
|---|---|---|
| `/requests` | The list. `?archived=1` for the archive | [`pages/research-requests.md`](pages/research-requests.md) |
| `/requests/new` | The commission form — 20 fields, 5 groups | " |
| `/requests/{id}` | One request, and what has been done to it | " |
| `/requests/{id}/edit` | The same form, prefilled. Refused once a run exists | " |
| `/requests/{id}/remove` | A confirmation *page*, listing what would be destroyed | " |
| `/requests/{id}/assumptions` | The per-request assumptions surface | [`pages/research-gates.md`](pages/research-gates.md) |
| `/requests/{id}/assumptions/{id}` | One assumption, its justification, its history | " |

### Research — the run

| URL | What it is | Spec |
|---|---|---|
| `/runs/{id}` | **The console.** Steps, status, spend, the live feed | [`pages/research-run-console.md`](pages/research-run-console.md) |
| `/runs/{id}/plan` | **Gate 1** — approve the plan. Always fires | [`pages/research-gates.md`](pages/research-gates.md) |
| `/runs/{id}/financials` | Gate — confirm the extracted financials | " |
| `/runs/{id}/sector` | Gate — confirm what kind of business this is | " |
| `/runs/{id}/peers` | Gate — confirm the comparison set | " |
| `/runs/{id}/themes` | Gate — confirm the themes | " |
| `/runs/{id}/assumptions` | Gate — confirm the valuation's inputs | " |
| `/runs/{id}/review` | **Gate 3** — approve the draft. Always fires. The largest page in the product | " |
| `/runs/{id}/preview` | The draft as the finished document — no navigation, print stylesheet | " |
| `/runs/{id}/summary` | The document narrowed to one page | " |
| `POST /runs/{id}/gates/{gate}` | Record a decision | " |
| `POST /runs/{id}/cancel` | Ask a run to stop at its next step boundary | [`pages/research-run-console.md`](pages/research-run-console.md) |
| `POST /runs/{id}/replay` | Re-derive everything and report what still holds | [`pages/research-evidence.md`](pages/research-evidence.md) |

### Research — the evidence

| URL | What it is | Spec |
|---|---|---|
| `/runs/{id}/sources` | Every document, its tier, its dates, its hash, its flags | [`pages/research-evidence.md`](pages/research-evidence.md) |
| `/runs/{id}/claims` | The claim index a reader arrives at from a report | " |
| `/claims/{id}` | The sentence, the figure, and the exact words behind it | " |
| `/runs/{id}/footnotes/{n}` | The walk back from a marker | " |
| `/runs/{id}/valuation` | Both terminal methods, the sensitivity grid, the comps | " |
| `/calculations/{id}` | The arithmetic, and every input's origin | " |

### Research — reports, skills, knowledge

| URL | What it is | Spec |
|---|---|---|
| `/reports` | Every report this account produced, grouped by company | [`pages/research-reports.md`](pages/research-reports.md) |
| `/reports/{id}` | The report as approved, with its hash | " |
| `/reports/{id}/preview` | Its HTML notation | " |
| `/skills` `/skills/new` `/skills/examples` `/skills/import` `/skills/{key}` | The methodology library and its editor | [`pages/research-skills-and-knowledge.md`](pages/research-skills-and-knowledge.md) |
| `/knowledge` `/knowledge/graph` `/companies/{id}` | What the platform knows, drawn and measured | " |

### Portfolio

| URL | What it is | Spec |
|---|---|---|
| `/portfolio` | The book as at a date, plus the transaction form | [`pages/portfolio.md`](pages/portfolio.md) |
| `POST /portfolio` | Create the first book | " |
| `POST /portfolio/transactions` | Record one thing that happened | " |

---

## The four journeys

### 1. Commission a report, and approve it

**The spine of the product.** Tens of minutes end to end, most of it waiting, punctuated by
between two and seven decisions that each cost money to get wrong.

```
/  →  /requests/new  →  /requests/{id}  →  POST /runs  →  /runs/{id}
                                                              │
                    ┌─────────────────────────────────────────┘
                    ▼
   /runs/{id}/plan       GATE 1 · always · ~£0.15 spent · approving commits the rest
                    │
                    ├─ /runs/{id}/financials   conditional
                    ├─ /runs/{id}/sector       conditional · changes which models may run
                    ├─ /runs/{id}/peers        conditional
                    ├─ /runs/{id}/themes       conditional
                    ├─ /runs/{id}/assumptions  conditional · approves work not yet done
                    ▼
   /runs/{id}/review     GATE 3 · always · the draft, its validation, its cost, its critic
                    ▼
   /reports/{id}         frozen, hashed, exportable
```

**Where it goes wrong today.** The operator returns to the console after every gate, and the
console does not show them what the gate they just left was about, nor what the next one will
be. There is no sense of progress through a sequence — only a list of steps and a banner
saying something is waiting. Seven decisions presented as seven unrelated interruptions.

**The single largest opportunity in this brief** is to make this feel like one journey with a
known shape, without pretending the shape is fixed — five of the seven gates are conditional
and the platform genuinely does not know which will fire until it gets there.

### 2. Check a figure

**What the product is for.** A reader doubts a number and walks it back to bytes.

```
a figure in the report
   → its footnote marker
      → /runs/{id}/footnotes/{n}
         ├─ a source marker      → the excerpt, verbatim, + the verifier's verdict + the hash
         └─ a calculation marker → /calculations/{id} → the formula, the inputs
                                       → each input's own origin, recursively
```

**The two-click standard is the design target**, and it is met in places today. From any
figure, the *first* click says where it came from; the *second* shows the evidence itself.

**Where it goes wrong today.** The walk works but the surfaces along it look like debugging
output. They are dense tables of identifiers and hashes with no visual hierarchy, so the one
piece of information the reader wants — *does this check out?* — is not the most prominent
thing on the page. See [`pages/research-evidence.md`](pages/research-evidence.md).

### 3. Record and reconcile a book

```
/portfolio (empty) → create the book → /portfolio
                                          ├─ record a transaction  (the form, below the table)
                                          └─ set the as-at date    (a GET, so the view is a link)
```

**Reconciliation against a broker statement is the only external check this tool has**, and a
statement arrives dated — which is why the date control exists and why it is a `GET`. "As it
stood on the thirtieth" is a link the operator can keep.

**Where it goes wrong today.** Four tiles, a table and a form on one page, with no return
figure, no exposure breakdown and no way to see the transactions that produced any row.
See [`pages/portfolio.md`](pages/portfolio.md).

### 4. Come back after a week

```
/  →  "Your attention"  →  a row  →  Preview (drawer)  →  Open the run
```

**The most under-designed journey with the most upside.** The work list groups items by
severity — blocked, broken, idle — and each row is a real sentence naming the company and
what it wants. A drawer gives a preview without losing your place.

The machinery is good. What is missing is any sense of *time* — nothing says how long
something has been waiting, or how much it has already cost, or whether it is still worth
finishing.

---

## Navigational rules that survive a redesign

**Every page is reachable, or explicitly declared unreachable.** A test enumerates every
route and fails unless it is either in the navigation or in a list of pages named as reachable
only from inside another page. There is no such thing here as a page somebody can only find
by knowing the URL.

**Deep links are real links.** A gate, a claim, a calculation, a footnote and a portfolio
date are all addressable and all meant to be bookmarked and shared with oneself. Nothing
important lives only in a scroll position or a panel state.

**Back always works.** No page traps the reader, and no flow depends on forward-only
progress. A gate decision is a POST followed by a redirect, so refreshing never re-submits.

**A destroyed thing gets a page, not a dialogue.** The removal confirmation lists what would
be destroyed, with counts, because a `confirm()` box cannot hold them and a decision that
irreversible should not be made from a browser chrome popup.

---

**Next:** [the per-page specifications](pages/) · [the design
system](03-design-system.md)
