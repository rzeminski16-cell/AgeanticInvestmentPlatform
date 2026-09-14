# Skills and Knowledge

*Two side-tools inside the research section. Neither is on the run journey; both are where an
operator goes deliberately.*

---

# Part 1 — Skills

**`/skills`** and its editor: user-authored methodology files that shape how runs work.

## At a glance

| | |
|---|---|
| **URLs** | `/skills` · `/skills/new` · `/skills/{key}` · `/skills/examples` · `/skills/import` · `/skills/{key}/export` |
| **Who arrives** | The operator, encoding their own method — how they want a section written, what a valuation should always consider |
| **What they came for** | To write, edit, test or import a skill |
| **Templates** | `skills/list.html` (36 raw ramps) · `edit.html` (**87**) · `import.html` (39) · `examples.html` (13) |

## The job

**Let a person write instructions that shape a run, and show them exactly what those
instructions will and will not be permitted to do.**

That second half is the whole design problem. A skill is user-authored text that reaches a
language model, and the platform's rule is absolute:

> **Skill files are additive-only. User-authored instructions may add requirements, never
> relax them.** No wording in a skill file can switch off citations, set a rating, or bypass
> point-in-time rules.

This is enforced structurally rather than by prompt text — a corpus of attack files must all
fail. **So the editor's job is not only to let somebody write a skill; it is to make the
boundary visible, so a rejection reads as a rule rather than as the tool being awkward.**

## What is on it

**`/skills`** — every saved skill, its current version, and whether runs will pick it up.

**`/skills/{key}`** — the editor, open on the current version's **exact source bytes**.
Round-tripping the stored source rather than re-serialising the frontmatter is what makes an
edit an edit: the content hash is over the file as written, so a reformatted version would be
a different skill by the platform's own reckoning.

Four blocks: the source, the issues, **What this composes to** — the effective policy after
composition for a custom section; for a methodology, house view or preference, the roles
the text composes into and the statement that it reaches no role that judges (ADR 0108) —
and **Try it against a finished run**.

**Validate** writes nothing and re-renders with either the issues or the composed policy.
**Dry run** executes the skill against a chosen finished run and shows what it produced —
its own run, against another run's evidence.

**`/skills/import`** — two steps in one route, and the difference is the confirmation. Without
one, the diff is shown and nothing is written. With one, the hash is recomputed from what is
stored *now* — an import confirmed against a version that has since moved is not a
confirmation of this one.

**`/skills/examples`** — worked examples, **listed rather than installed**. An example reaches
the platform through the ordinary import path, diff and confirmation included. Pre-installing
them would make that step look optional, which is the habit the diff exists to prevent.

**`/skills/{key}/export`** — the stored source, byte for byte, as a file. The *source*, not a
re-serialisation: what comes back must be what would go in, or a round trip would rewrite the
operator's file — reordering keys, dropping comments — and the import diff would show changes
nobody made.

## Inputs

| Control | Type | Notes |
|---|---|---|
| Skill source | Textarea, raw file bytes | Frontmatter plus body |
| Validate | Submit | Writes nothing |
| Save | Submit | Or re-renders with the reason it could not be saved |
| Enable / disable | Submit | Whether runs pick it up |
| Import | File, then confirm | Diff first, always |
| Dry run | Select a finished run, submit | Executes; shows output |

## States

Empty library · a skill with issues · a valid skill · disabled · an import diff · an import
whose base version moved · a dry run's output · a dry run with nothing to run against.

## What is wrong today

**It is a textarea.** Editing a structured file with frontmatter, in a plain textarea, with
validation only on submit. No syntax cue, no structure, no indication of which fields are
required until it is rejected.

**"What this composes to" says where the text reaches, not what it becomes on the day.**
For the three prompt kinds the right-hand pane names the roles; what the text composes to
on the day is the last block of the planner's and the writer's user turn, which nothing on
this page shows.

**A run that used a skill says so on the editor, not on the run.** The editor lists the runs
that pinned the skill; the run console does not yet point back at the skills its plan
pinned, beyond gate 1's list.

## What to improve

**1. State the boundary on the editor, always** — done. The same sentence the library leads
with, as the editor's own verdict, on a new file and on a saved version alike.

**2. Promote the composed policy** — done earlier: two panes, what you wrote on the left and
what runs receive on the right, sticky at width.

**3. Make the dry run a first-class action** — done. *Try it* on the library row for a
custom section, and a link at the top of the editor to the dry-run block.

**4. Give the source field structure.** Even without a code editor — which would be a
JavaScript island needing its own record — the frontmatter fields could be fields.

**5. Show a skill's effect** — done for the runs: every run whose plan pinned the skill, at
the version it pinned, planned in or set aside with the reason, each linking to the run.
What changed in the section is the run's to show.

**6. The composed text for prompt kinds.** See above: the last block of the user turn, as
the planner and the writer would receive it.

## What must not change

**The stored source round-trips byte for byte.** Export is the source, not a re-serialisation.

**An import shows a diff before it writes**, and re-checks the hash on confirmation.

**Examples are listed, never pre-installed.**

**Validation writes nothing.**

**The additive-only rule is structural.** No interface affordance may imply otherwise.

**A skill never names its readers.** The role table is the platform's (ADR 0108); no field on
the editor, and no frontmatter key, may offer a choice of roles.

---

# Part 2 — Knowledge

**`/knowledge`**, its graph, and a company's history.

## At a glance

| | |
|---|---|
| **URLs** | `/knowledge` · `/knowledge/graph` · `/companies/{id}` |
| **Who arrives** | The operator, asking what the platform has accumulated |
| **Templates** | `knowledge/index.html` (44 raw ramps) · `graph.html` (12) · `companies/detail.html` (62) |

## The job

**Say what the platform knows, how well connected it is, and how stale it is getting.**

## What is on it

**`/knowledge`** — the graph measured. Six blocks: **Size · Shape · Coverage · Assumption
accuracy · Freshness** (with *Not revisited recently* and *Catalyst windows that have closed*)
**· Vault**.

**Not scoped to the signed-in account, deliberately.** The graph is built from every approved
report in the database, and a per-user view of a shared graph would report a connectivity that
does not exist.

**`/knowledge/graph`** — the confirmed relations, drawn. **Static SVG, laid out in Python,
with no script at all**: the page carries coordinates, not code, so what the browser shows is
exactly what the rows say, and the same nodes and edges always produce the same picture —
which is what lets a test hold the drawing.

> **This is the one place a JavaScript island is pre-authorised in principle.** If it needs
> only to be bigger and clickable, it stays server-drawn — those are a viewBox and an anchor.
> Pan, zoom, drag and live filtering would be the island: the layout stays server-computed,
> the component receives placed nodes as JSON and owns only the viewport transform, and no
> figure passes through it. **If your design wants this, ask for it** — see the challenge
> appendix in [`../01-constraints.md`](../01-constraints.md).

**`/companies/{id}`** — one company's history: a timeline, **Valuation history**, **Approved
reports**, and **Prior catalysts, and what happened** — with a form to record the outcome of a
catalyst whose window has closed.

**That form is the operator's answer, never a model's.** The label must name a catalyst an
approved report actually proposed; the reason must not be blank. Approved reports only,
because this is the history a decision could rest on.

The valuation chart is deterministic and salted with the company id, so the page shows the
same bytes on every load.

## Inputs

| Control | Where | Notes |
|---|---|---|
| Record what happened | `/companies/{id}` | A catalyst outcome. Label must match a proposed catalyst; reason required |

## States

An empty graph (a new installation) · a graph too small to say anything · catalysts with open
windows · catalysts with closed windows awaiting an outcome · a company with one report ·
a company with several.

## What is wrong today

**The graph is a picture with one way in.** Nodes are links and the relations are listed
in words beneath, but a graph of any size still cannot be focused: no filter, no way to
see one company's neighbourhood alone.

**The company page's verdict is the latest report's.** It says what was last thought and
when; whether anything has happened since — a filing, a finding on a thesis about the
company — is on other pages.

## What to improve

**1. Lead the knowledge page with what to do** — done: the verdict answers whether the
knowledge is still useful, and *Close the loop* — companies not revisited, catalyst windows
that have closed — is the first sheet, each row a link to the company.

**2. Make graph nodes links** — done; a company node is an anchor to its history page.

**3. Design the empty and near-empty states** — done: an empty graph says what would fill
it, and nodes with no relation between them are said to be too sparse to read, with where
the lines come from.

**4. Give the company page a verdict line** — done, as *The standing view*: what we last
thought and when.

**5. Raise catalyst resolution** — done: *Record what happened* is the first sheet on the
company page whenever a window has closed and nothing is recorded.

**6. Focus in the graph.** One company's neighbourhood alone, which is a server-drawn
picture with a filter in the URL before it is an island.

**7. "Since then" on the company verdict.** Filings and findings since the last approved
report, once the monitor's findings are keyed by company.

## What must not change

**The graph is laid out on the server.** Coordinates, not code. Deterministic.

**Knowledge measurements are unscoped**, and the page should not imply they are personal.

**Only approved reports reach the history surfaces.**

**A catalyst outcome is the operator's**, must name a real proposed catalyst, and must carry a
reason.

**The valuation chart is deterministic.** Same rows, same bytes, every load.

## Done when

- The knowledge page answers "is this getting better, and what should I do about it?" above
  the fold.
- The graph can be explored, at minimum by clicking a node.
- Empty and sparse states say what would fill them.
- Recording a catalyst outcome feels like completing something rather than filing something.
