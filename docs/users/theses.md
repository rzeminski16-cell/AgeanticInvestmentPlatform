# Theses

Writing down what you believe about a company, and what would show you were wrong.

> **This is a record of your own views, not evidence and not advice.** Nothing you write here
> feeds a number anywhere in the platform. A thesis is what you thought, when, and why — kept
> so that later you can see whether you were right for the reasons you gave.

---

## The idea in one line

**A thesis is its premises, and every premise says what would defeat it.**

You write a title and pick the company it is about. Then you add premises one at a time:
a claim, the basis you hold it on, and either a threshold the platform can test or a date you
will look at it again by. The platform keeps every premise you ever wrote, including the ones
you later gave up, with the reason you gave.

## Writing one

Open **Theses** from the menu. The form asks for:

- **Title** — what the thesis claims, in a line. *"Azure keeps compounding above 25%"*, not
  *"Microsoft"*.
- **About** — a company the platform has researched. If the list is empty, commission a
  research request first; a thesis about a ticker nobody has looked up would be a view about
  a string.
- **Written against** — the approved report you read before writing this, if there was one.
  It must be about the company above; the thesis keeps the link so a later reader can see what
  you had in front of you. The field only appears once a report has been approved.
- **Written on** — defaults to today. Backdate it if you are writing up something you already
  held, because "what did I believe before the results" is the question this tool answers.

## Adding a premise

Each premise has three parts:

1. **The premise** — one claim about the company.
2. **On what basis** — what you read, saw or reasoned. This is required: a view with no stated
   basis is a guess wearing a label.
3. **What would defeat it** — one of two answers:
   - **A threshold code can test.** A metric, a comparator in words (*at least*, *above*, …),
     a number and its unit. *"Revenue growth at least 25%."* The monitor tests it against
     filings as they arrive; the arithmetic is the platform's, not yours and not a model's.
     The metric is chosen from the ones the monitor can measure, grouped as ratios (*Gross
     margin*), growth on the year before (*Revenue growth*) and lines as filed (*Revenue*) —
     nothing it cannot measure is offered.
   - **A person will look again.** A date. For the premises no filing measures — the quality
     of the people, the durability of the advantage. These are not lesser premises; they are
     usually the ones that decide whether a position works, and the platform refuses to let
     you invent a number for them.

Choosing one answer puts the other's fields away; with scripting off both stay on the
screen, and the answer you chose is still the one that counts.

The threshold's unit is required, and chosen from the ones the monitor can compare: per cent,
times (a multiple), days, or a currency. A bare number cannot be compared with a fact — a
threshold in per cent must say so, or it will one day be compared against a figure in dollars.

## Reading the page

The page is called **What you believe**, and the premises are the page. Each shows its sentence,
its test — *Defeated if* the metric, the comparator and the threshold, or *No test* and the date
you will look again — and a label saying what the monitor last read:

- **holds** — the last reading measured the test and it held.
- **broke** — the last reading measured it and it did not hold.
- **by hand** — nothing files a number for this one; you are asked on its date.
- **not read yet** — a test no annual filing has been read against since you wrote it.
- **cannot be measured** — the monitor tried and could not; the line under it says why.

The line under each test says what was measured, for which year and when it was read —
*"Measured 4.1% for the year to 31 December 2025, read on 11 September 2026."* Nothing is
measured on the page: the label is the monitor's last reading, never a fresh one.

The panel beside the page counts what the thesis amounts to: when it was written and against
which report, how many revisions, how many decisions rest on it, and how many premises carry a
test. At least one should, or the monitor has nothing to watch on your behalf. A thesis with none
saves, and the panel says what that costs.

## What the page shows around a thesis

A thesis is about a company, and two other tools know that company.

- **Reports on the company.** Every approved report about it, newest first, each a link.
  The one you named as *written against* is marked. A report approved after the thesis was
  written is listed too: it is the thing to read these premises against next.
- **The position.** What your book holds, or held, in the company. An open position links to
  the [portfolio](portfolio.md); a closed one links to its [review](review.md), or to the
  review list if it has not been reviewed yet. A book that has never dealt in the company
  says so, and points at [decisions](decisions.md), because a position starts with one.

Neither is a link the thesis depends on. Both are looked up from the company each time the
page is opened, so a report or a trade recorded later appears without anything being edited.

## Changing your mind

**Revise a premise** by changing its sentence or its test where it stands on the page, saying
why in the box below the premises, and pressing **Save the revision**. Everything you changed
is saved at once, with that one reason. The old wording is not overwritten: it is withdrawn with
your reason, and the new wording takes its place with the same grounds. Open *How this premise
has changed* under it and the history reads as a story — *"On 3 March 2026 you believed: …
On 14 September 2026 you revised it, because …, to: …"*. A premise keeps its kind: to give a
date-only premise a test, or the other way round, withdraw it and add the other.

**Withdraw a premise** from **Withdraw a premise** at the top of the page, with a reason. It
stops being tested and moves to *Given up* at the foot of the premises, struck through, with the
reason. Nothing is deleted.

**When a premise breaks**, a panel under the premises says so and offers three answers, each
saying what it does:

- **Revise it** takes you to the premise, to write what you now believe.
- **Withdraw it** gives it up with a reason.
- **Keep it — say why** records that you think the miss is temporary. The premise stands and
  the next annual filing is read against it like any other.

Whichever you choose closes the monitor's question about it with the same reason — at the
thesis gate where the reading's pass is still on record — so the [monitor](monitor.md) and
Today stop asking.

**Retire a thesis** with a reason. It moves to the retired list, keeps every premise, and takes
no new ones. Write a new thesis rather than editing a retired one.

Both are on the audit trail, with the thesis as their subject, so what you believed and when
you stopped believing it are both on the record.

## What this tool does not do

- It does not store a conviction, a confidence or any score. A number under that name would be
  a view dressed as a figure, and the platform's rule is that a judgement is never a source for
  anything (ADR 0074).
- It does not test a premise against the share price. Price is an outcome, not evidence about
  a premise (ADR 0079).
- It does not decide anything. The [monitor](monitor.md) raises questions about premises;
  answering them is yours.
