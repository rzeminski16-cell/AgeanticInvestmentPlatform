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
     a number and its unit. *"Revenue growth at least 25 percent."* The monitor tests it
     against filings as they arrive; the arithmetic is the platform's, not yours and not a
     model's. The metric field offers the names the monitor understands — a ratio such as
     `operating_margin`, a line's growth such as `revenue_growth`, a line's level such as
     `revenue` — and still takes any words you type. A metric the monitor does not know is
     recorded, and read as unobservable when the monitor runs.
   - **A person will look again.** A date. For the premises no filing measures — the quality
     of the people, the durability of the advantage. These are not lesser premises; they are
     usually the ones that decide whether a position works, and the platform refuses to let
     you invent a number for them.

Choosing one answer puts the other's fields away; with scripting off both stay on the
screen, and the answer you chose is still the one that counts.

The threshold's unit is required. A bare number cannot be compared with a fact — a threshold
in per cent must say so, or it will one day be compared against a figure in dollars.

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

**Withdraw a premise** with a reason. It stays on the page, struck through, with the reason
beneath it. Nothing is deleted.

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
