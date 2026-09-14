# Content and voice

*On this platform the words are part of the design. A refusal that reads as a bug and a
refusal that reads as a guarantee are the same code and a different product.*

---

## Why this is a design document rather than a copy deck

Most of what this interface says is one of four things: **what it is about to do**, **what it
found**, **what it will not do and why**, or **what it is waiting for you to decide**. Three of
those four are unusual, and none of them survives being written as generic UI copy.

**The refusals in particular are the product.** A tool that refuses to value a bank with a
discounted cash flow, refuses to show a total when one position cannot be priced, and refuses
to substitute a US Treasury yield for a Bank of England rate is a tool making a claim about
its own trustworthiness — and every one of those refusals reaches the reader as a sentence on
a screen. Written well, it is the moment the operator decides the tool is serious. Written
badly, it is an error message.

So: **do not treat the strings as placeholder text.** Most of them are more carefully
considered than the layout around them, and several are better than anything a redesign will
replace them with.

---

## The rules

### UK English, everywhere

Colour, not color. Organisation. Recognise. Behaviour. Licence (noun) and license (verb). This
applies to user-facing text, documentation, comments and commit messages.

### Say what happened, not what the system did

| Instead of | Say |
|---|---|
| "An error occurred" | "The database refused that transaction: a sale must be entered as a positive number" |
| "Invalid input" | "That as-of date is in the future. The analysis is performed as at this date" |
| "No results" | "No run is stopped at a gate, nothing failed, and every request you have written has been run" |
| "Operation failed" | "This run has drafted nothing yet. There is nothing to approve" |

### An empty state names what you would get by acting

Never report the absence the reader can already see. An empty state is the one place a page
has the reader's full attention and nothing to distract them.

The current best example, worth matching:

> **Nothing is waiting** — No run is stopped at a gate, nothing failed, and every request you
> have written has been run. Start another when you are ready. *[Commission research]*

It names what was checked — so the reader can trust the answer — and it offers the next action.

The current worst example, worth fixing: an empty `/reports` list, which is a plausible first
visit and says nothing.

### A refusal names the remedy

Every refusal should answer "so what do I do?".

> "This platform holds no priced listing for 'VOD'. A listing is created when a research run
> acquires prices for a company; commissioning a report on this ticker is what makes it
> dealable. Holding a security the platform cannot price would refuse the whole net asset
> value rather than only that row."

Three sentences: what happened, what would change it, and why the rule exists. **That is the
shape.**

### Distinguish a refusal from a failure

They look alike and mean opposite things.

- **A refusal is the platform working.** A bank refused a discounted cash flow, a run stopped
  at its cost ceiling, a total withheld because a position could not be priced. These should
  read as competence.
- **A failure is the platform broken.** A step errored, the database is unreachable, an
  extraction crashed. These should read as faults, with a way forward.

The run console already does this well for the budget case: *"the next step would take this
run past a spending cap, so it stopped before making the call rather than after paying for
it."* Nothing there is apologetic, because nothing went wrong.

### Address the operator in the second person, where the item is addressed to them

The work list does this deliberately. Every gate has a phrase written for it:

| Gate | Phrase |
|---|---|
| Plan | "approve its research plan" |
| Unmapped concepts | "decide about the figures nothing could map" |
| Peer set | "confirm its peer set" |
| Sector | "acknowledge that the standard model does not fit its sector" |
| Themes | "confirm the themes it belongs to" |
| Assumptions | "confirm the assumptions its valuation will be built on" |
| Budget | "decide whether it may spend more than its ceiling" |
| Final | "review the finished report" |

So a row reads *"Contoso plc is waiting for you — The run stopped so you could confirm its peer
set."* **Every member of the gate vocabulary has a phrase, enforced by a test**: a gate added
without one is a red build rather than a run described as waiting for nothing in particular.

### Do not explain what the reader already knows

They know what a discounted cash flow is. They do not know what *this run* did with one. Spend
the words on the second.

### Name a thing the same way everywhere

Existing vocabulary that must stay consistent:

| Term | Means |
|---|---|
| **Run** | One execution of a workflow. Has steps, gates, a cost and a status |
| **Request** | The commission. A mandate, not an execution |
| **Gate** | A place a run stops for a person |
| **Claim** | One assertion in a report, with evidence behind it |
| **Artefact** | Hashed bytes as fetched |
| **Source** | A document a run acquired |
| **Attestation** | Something the operator asserted about their own book |
| **Grade** | How strong the evidence under an attested figure is: `Typed` or `Documented` |
| **Book** | A portfolio |
| **Skill** | A user-authored methodology file |

**Two words are deliberately kept apart and must stay apart.** *Attested* is a provenance
class — a figure whose origin is the operator's own book. *Typed* is a grade — how strong the
evidence is. A documented attestation is fully attested and not typed. Two vocabularies
sharing one word teach a reader that the word means neither.

### Never let a number appear without saying what kind it is

Every figure is a source fact, a calculation, an attestation, an assumption or a judgement.
Where there is any doubt, the badge says which, and the badge is a link.

### Money is written by the server, always

Including the currency symbol, the separators and the rounding. A total that rounds to nothing
says `under £0.01` rather than `£0.00`, because "we have spent nothing this month" and "we
have spent a third of a penny" are different answers and only one is true.

**Two money renderings exist and must not be confused.** The report's house style renders in
millions — right for a company's revenue. The portfolio renders exact to the penny — because
that screen is reconciled line by line against a broker statement, and a figure rounded to the
nearest million cannot be reconciled against anything.

### The disclaimer

"This is not investment advice." Not negotiable, not removable, once per page.

### Keep the platform's voice out of the report

A rule for the *document* rather than the screens, worth knowing because it explains a pattern
you will see: every sentence in a report that was *about the report* has been removed or moved
to where disclosure belongs. A report is about a company. The failure mode returns whenever a
new refusal path gets a placeholder written in the platform's voice rather than the report's.

---

## Where the writing is already strong

Worth reading before writing anything new. These are the standard.

- **The budget-cap notice** on the run console — the whole "this is not a failure" register in
  three sentences, twice, worded differently for the per-run and monthly cases.
- **The transaction form's hints** — *"Enter every amount positive. A sale, a fee and a
  withdrawal are signed for you."* and *"In the dealing currency — pence for a London listing,
  if that is what the contract note says."*
- **The dual-listing refusal** — names both choices and says why picking one would be wrong.
- **The unmapped-concepts gate** — every column earns its place in answering one question.
- **The "already decided" notice** — *"A decision is not a state to be re-asserted; changing it
  needs a new run."*
- **The point-in-time hint** — *"Turning this off invites look-ahead bias, which silently
  invalidates backward-looking analysis. Leave it on unless you have a specific reason not
  to."*
- **The console's waiting note** — *"A step that calls a model routinely takes two to five
  minutes: the model reasons before it writes, and nothing is recorded until it has
  finished."*

## Where it is weak

- **Status values reach the screen as raw enums.** `AWAITING_APPROVAL`, `RUNNING`,
  `UNMAPPED_CONCEPTS`. Every one needs a human phrase, and the gate vocabulary above shows the
  form the phrases should take.
- **Step keys reach the screen as identifiers.** `red_team`, `classify`, `acquire`. Nineteen of
  them are the main content of the run console.
- **Several empty states do not exist**: an empty report history, an empty knowledge graph, a
  first-run main menu.
- **The payload-hash guarantee is written as a technicality** when it is the strongest promise
  the interface makes.

---

**Next:** [accessibility](05-accessibility.md)
