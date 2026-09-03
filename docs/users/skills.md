# Skills: the methodology library

Your own method, written down once and carried into every run that follows: what to
analyse, what to weigh, how to present it — and, where the platform's sections do not
say what you need said, a section of your own.

> **A skill may add requirements. It cannot remove citations, set a rating, or relax
> point-in-time rules.** That is enforced in code, not by wording, and a corpus of attack
> files proves it on every build.

---

## The idea in one line

**A skill is versioned, pinned to a run at gate 1, and composed into the roles that plan
and write — and into no role that judges.**

## Four kinds

A skill is a Markdown file with a short frontmatter. The `kind` decides what it does.

| Kind | What it is | Where it reaches |
|---|---|---|
| `methodology` | How you analyse and what you weigh — *"weight owner-operator alignment heavily"* | the planner, the section writer |
| `house_view` | A standing view the run must test against the evidence — *"rates stay higher for longer"* | the planner, the section writer |
| `preference` | How you want things presented — *"state valuation conclusions in sterling"* | the section writer |
| `custom_section` | A section of your own, with an evidence policy and an output contract | its own section |

The first three produce no section. They are composed, after everything the platform
says and before the quoted evidence, into the two roles that plan and write. They reach **no adversary**: not the critic that
challenges the plan, not the red team that attacks the draft, not the verdict that judges
the report. A red team handed your priorities would attack the draft on your own terms,
which is confirmation bias with a budget. That is a rule of the platform, and a skill
cannot change it — there is no field for naming its readers.

## Writing one

Open **Skills** from the research tool and choose **Write a new skill**, or start from an
example on the **Examples** page — there is one of each kind, and an example is imported
through the ordinary path, diff and confirmation included, never installed behind your
back. The editor validates without saving, and for a custom section it shows **what this
composes to**: the effective policy after the floor is applied, with every clamp named.
For a methodology, house view or preference it says instead which roles will read the
text, because there is nothing to clamp: the file may not declare an evidence policy,
tools, a budget or an output, and one that tries is refused at authoring with the line.

Write the body for the reader you have in mind. The planner reads it as a plan is
proposed; the section writer reads it with every built-in section. Neither will quote it
or refer to it, and neither can act on any sentence that would relax a rule above it.

## Versions and pins

Every save is a new version, and the old one stays. When a run reaches its plan step it
**pins** every enabled skill at its current version — before the planner is asked, so the
planner, gate 1 and every section run under the same rows. Gate 1 lists them under **Your
skills on this run**, with the version, and for a methodology, house view or preference
the roles it composes into. Approving the plan approves that.

Editing a skill after approval changes nothing about the run. A run restarted after a
failure re-plans against the current versions, so a fix reaches it; a run in flight keeps
the versions it was approved with.

## Trying a custom section

**Dry run** executes a custom section against a finished run's evidence, in a run of its
own, and shows what it produced. It spends against the finished run's request and is the
safe way to find out before the next real run does. The three prompt kinds have no
section to try; what they do is visible on the plan and in the sections of the next run.

## From a lesson to a skill

`uv run aer lessons` prints what the critique loop keeps having to revise, grouped by class
across runs. The platform never acts on a lesson: a recurring class becomes standing
guidance only when you write it down as a methodology skill and enable it. That is the
whole route, and it is deliberate — a person decides a lesson is real before any future
run inherits it.

## What this tool does not do

- It does not let a skill relax anything. Additive only, enforced in code.
- It does not let a skill choose its readers. The table is the platform's.
- It does not compose a skill into an adversary, an extractor, a validator, the risk
  analyst or the post-trade reviewer.
- It does not install examples. Every skill on the platform is one you imported or wrote.
