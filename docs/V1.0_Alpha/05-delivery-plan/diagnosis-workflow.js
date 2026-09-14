export const meta = {
  name: 'readiness-remediation-plan',
  description: 'Diagnose why the research tool is not user-ready and not better than the Claude console, then design a sequenced plan of action to fix both',
  phases: [
    { title: 'Diagnose', detail: 'one reader per gap area, grounded in the audit records and the code' },
    { title: 'Challenge', detail: 'adversarial check of each diagnosis against the code, the ADRs and the invariants' },
    { title: 'Plan', detail: 'four independent full plans from the verified diagnosis, each with a different framing' },
    { title: 'Score', detail: 'three judges rank the plans and say what to graft from the runners-up' },
    { title: 'Critique', detail: 'completeness critics: what no plan covers' },
  ],
}

const CONTEXT = `
CONTEXT — read this before anything else.

The repository is an auditable equity research platform (see CLAUDE.md, already injected).
A full readiness audit was completed on 2026-09-12 and is committed at
docs/plan/readiness-audit-2026-09.md (1,211 lines), with its raw results in
docs/plan/readiness-audit-2026-09/ (run exports, reports, baselines, scores, judges' rubrics,
screenshots). Read what you need of it; do not re-measure anything, and never invent a figure.

The audit's verdict: "Not ready for general use; ready for one thing, and good at it."
 - Accurate: YES. 0 contradicted of 779 checkable figures across five reports; 259/259
   citations verified; 3,335 calculation rows replayed with zero divergence.
 - Budget friendly: YES. £7.48 / £6.80 / £6.83 / £7.21 / £7.61 per run, average £7.19,
   against a £10 target; console baselines cost £7.63 / £11.03 / £6.71.
 - Reliable: NOT YET. Three of six runs reached an approved, rendered report.
 - Complete: NO. 18 of 18 sections on every run, but no peer multiple on any run, no segment
   revenue, no guidance, and market capitalisation withheld on three runs.
 - Practically: three independent judges read each document blind (an investor deciding with
   their own money, a reader handed only the file, a sceptic checking the work) and compared
   the pairs blind. ALL SIX blind comparisons chose the Claude console. Six of six judges
   said they would not act on the platform's report. The reasons repeated: the header says
   "Non-binding view: no view reached", the brief's focus questions are answered "partly" or
   not at all, and the figures a research note is read for are withheld.

THE OPERATOR'S REQUEST, which this workflow serves:
"It seems we have two major issues: (1) the system is not user ready in many ways, and
(2) the system is not better than Claude console. I want a plan of action to fix both —
fixes, new features, running, etc."

So there are exactly two goals, and every item you produce must serve one or both:
  ISSUE 1 — USER-READY: a competent private investor can commission, steer, finish, recover
  and share research without a terminal, without dead ends, and without meeting a defect.
  ISSUE 2 — BETTER THAN THE CONSOLE: the same three judges, reading blind, would choose the
  platform's document over a Claude console note on the same brief — or at least split.

HARD CONSTRAINTS you must respect in every recommendation:
 - The invariants in CLAUDE.md are not negotiable. Deterministic Python owns every number
   and every fact; the model owns planning, interpretation, challenge and writing. Never
   move a calculation into a prompt. Every externally derived fact traces to a hashed
   artefact. Code, not the model, confirms a citation. Point-in-time is enforced at
   acquisition. Units carry through arithmetic. Cost is metered and capped in code.
 - docs/plan/ROADMAP.md is the authority on scope; the ADRs in docs/adr/ outrank it. A
   change that alters an invariant or a recorded decision needs a NEW ADR drafted beside it,
   not just a code change. Say so explicitly when an item needs one.
 - This is a personal, local-first research tool, one report at a time, for one operator
   (a private investor). It is not regulated investment advice and every user-facing
   surface must say so. A "stated view" must stay inside that framing.
 - Work is done by one person driving an AI coding session. Express effort in sessions/days,
   not team-weeks. Live runs cost about £7 each and the operator's audit budget was £100.

YOU ARE IN A PLANNING PASS. Do not modify, create or delete any file. Do not run the
application, the worker, the database or any live model call. Read, reason, and return
structured findings. Ground every claim in a file:line reference or an audit section
number. If you cannot ground a claim, mark it as a hypothesis in the text.
`

const DIAG_SCHEMA = {
  type: 'object',
  properties: {
    area: { type: 'string' },
    summary: { type: 'string', description: 'Three to six sentences: what is actually wrong in this area and why it matters to one of the two issues.' },
    items: {
      type: 'array',
      description: 'The discrete, actionable findings in this area. Five to twelve is typical. Do not pad.',
      items: {
        type: 'object',
        properties: {
          id: { type: 'string', description: 'short-kebab-slug, unique within this area' },
          title: { type: 'string' },
          problem: { type: 'string', description: 'What a user or a reader actually experiences. Concrete.' },
          rootCause: { type: 'string', description: 'The cause in code or policy, with file:line where possible.' },
          evidence: { type: 'array', items: { type: 'string' }, description: 'Audit section numbers, finding IDs, file:line, or a quoted judge sentence.' },
          whichIssue: { type: 'string', enum: ['user-ready', 'better-than-console', 'both'] },
          options: {
            type: 'array',
            description: 'The genuinely different ways to answer this, not variations of one.',
            items: {
              type: 'object',
              properties: { name: { type: 'string' }, change: { type: 'string' }, cost: { type: 'string' }, risk: { type: 'string' } },
              required: ['name', 'change'],
            },
          },
          recommendation: { type: 'string', description: 'Which option, and why. If it is the operator\u2019s call rather than yours, say that and set operatorDecision.' },
          filesTouched: { type: 'array', items: { type: 'string' } },
          adrNeeded: { type: 'boolean' },
          adrSubject: { type: 'string', description: 'One line: the decision the ADR would record. Empty if none.' },
          tests: { type: 'string', description: 'The regression test that must fail before the fix and pass after.' },
          acceptance: { type: 'string', description: 'How we would KNOW it worked, measurably, on a real run or in the suite.' },
          effort: { type: 'string', enum: ['an hour', 'a session', 'a few sessions', 'a week or more', 'unknown'] },
          dependsOn: { type: 'array', items: { type: 'string' }, description: 'ids (this area or another) that must land first.' },
          operatorDecision: { type: 'boolean', description: 'true when only the operator can choose, per the audit \u00a78.' },
          impact: { type: 'string', enum: ['decisive', 'large', 'moderate', 'small'], description: 'decisive = on its own it flips a judge\u2019s verdict or unblocks a whole use case.' },
        },
        required: ['id', 'title', 'problem', 'rootCause', 'evidence', 'whichIssue', 'recommendation', 'filesTouched', 'adrNeeded', 'tests', 'acceptance', 'effort', 'operatorDecision', 'impact'],
      },
    },
    openQuestions: { type: 'array', items: { type: 'string' }, description: 'What you could not settle from the record, and what would settle it.' },
  },
  required: ['area', 'summary', 'items'],
}

const CHALLENGE_SCHEMA = {
  type: 'object',
  properties: {
    area: { type: 'string' },
    verdicts: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          id: { type: 'string' },
          verdict: { type: 'string', enum: ['stands', 'weakened', 'refuted'] },
          why: { type: 'string' },
          correction: { type: 'string', description: 'The corrected root cause, recommendation, or effort. Empty if none needed.' },
          invariantRisk: { type: 'string', description: 'Name the invariant or ADR this recommendation would breach, or empty.' },
        },
        required: ['id', 'verdict', 'why'],
      },
    },
    missed: { type: 'array', items: { type: 'string' }, description: 'What the reader of this area failed to look at, stated as an actionable item.' },
  },
  required: ['area', 'verdicts'],
}

const PLAN_SCHEMA = {
  type: 'object',
  properties: {
    framing: { type: 'string' },
    thesis: { type: 'string', description: 'One paragraph: what fixing these two issues actually means, and the single idea this plan is organised around.' },
    workstreams: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          name: { type: 'string' },
          issue: { type: 'string', enum: ['user-ready', 'better-than-console', 'both'] },
          whyItMatters: { type: 'string' },
          items: { type: 'array', items: { type: 'string' }, description: 'Diagnosis ids, plus any new item you are adding, each as "id — what changes".' },
          exitCriterion: { type: 'string', description: 'The measurable condition that closes this workstream.' },
          effort: { type: 'string' },
        },
        required: ['name', 'issue', 'whyItMatters', 'items', 'exitCriterion', 'effort'],
      },
    },
    order: {
      type: 'array',
      description: 'The running order, step by step. Each step is a sitting or a short series of them.',
      items: {
        type: 'object',
        properties: {
          step: { type: 'number' },
          what: { type: 'string' },
          why: { type: 'string', description: 'Why here and not later or earlier.' },
          gatedOn: { type: 'string' },
          liveRuns: { type: 'string', description: 'Which live runs this step needs, and on which subjects. "none" if offline.' },
          spendGbp: { type: 'number' },
        },
        required: ['step', 'what', 'why', 'gatedOn', 'liveRuns', 'spendGbp'],
      },
    },
    measurement: {
      type: 'object',
      properties: {
        issue1Test: { type: 'string', description: 'How we know "user-ready" is fixed. Measurable.' },
        issue2Test: { type: 'string', description: 'How we know "better than the console" is fixed. Measurable, and it must be the blind judge comparison or something stronger.' },
        target: { type: 'string', description: 'The number that constitutes success, stated before the work starts.' },
        reRunPlan: { type: 'string', description: 'Which subjects, how many runs, which baselines, what it costs.' },
        spendGbp: { type: 'number' },
        permanentMetrics: { type: 'array', items: { type: 'string' }, description: 'Which audit scorers should become run-time metrics, if any.' },
      },
      required: ['issue1Test', 'issue2Test', 'target', 'reRunPlan', 'spendGbp'],
    },
    operatorDecisions: { type: 'array', items: { type: 'string' }, description: 'What the operator must decide before or during, each with the options and your recommendation.' },
    newAdrs: { type: 'array', items: { type: 'string' }, description: 'One line each: the decision the ADR records.' },
    notDoing: { type: 'array', items: { type: 'string' }, description: 'What this plan deliberately leaves out, and why.' },
    risks: { type: 'array', items: { type: 'object', properties: { risk: { type: 'string' }, mitigation: { type: 'string' } }, required: ['risk', 'mitigation'] } },
    totalEffort: { type: 'string' },
    totalSpendGbp: { type: 'number' },
    firstThreeThings: { type: 'array', items: { type: 'string' }, description: 'If the operator only does three things, these three, in order.' },
  },
  required: ['framing', 'thesis', 'workstreams', 'order', 'measurement', 'operatorDecisions', 'newAdrs', 'notDoing', 'risks', 'totalEffort', 'totalSpendGbp', 'firstThreeThings'],
}

const SCORE_SCHEMA = {
  type: 'object',
  properties: {
    lens: { type: 'string' },
    scores: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          plan: { type: 'string' },
          evidence: { type: 'number' }, invariants: { type: 'number' }, sequencing: { type: 'number' },
          measurability: { type: 'number' }, realism: { type: 'number' }, coverage: { type: 'number' },
          total: { type: 'number' },
          fatalFlaws: { type: 'array', items: { type: 'string' } },
          whatToGraft: { type: 'array', items: { type: 'string' } },
        },
        required: ['plan', 'evidence', 'invariants', 'sequencing', 'measurability', 'realism', 'coverage', 'total', 'whatToGraft'],
      },
    },
    ranking: { type: 'array', items: { type: 'string' } },
    synthesisAdvice: { type: 'string', description: 'How you would build one plan out of these four. Be specific about which spine and which grafts.' },
  },
  required: ['lens', 'scores', 'ranking', 'synthesisAdvice'],
}

const CRITIC_SCHEMA = {
  type: 'object',
  properties: {
    gaps: {
      type: 'array',
      items: {
        type: 'object',
        properties: { what: { type: 'string' }, whyItMatters: { type: 'string' }, suggestedItem: { type: 'string' } },
        required: ['what', 'whyItMatters', 'suggestedItem'],
      },
    },
    wrongAssumptions: { type: 'array', items: { type: 'string' }, description: 'Assumptions the plans share that the audit record does not support.' },
    sequencingObjections: { type: 'array', items: { type: 'string' } },
    cheapestWinsMissed: { type: 'array', items: { type: 'string' }, description: 'High-impact, low-effort items nobody proposed.' },
  },
  required: ['gaps', 'wrongAssumptions', 'sequencingObjections', 'cheapestWinsMissed'],
}

const AREAS = [
  {
    key: 'judges',
    title: 'Why every blind comparison chose the console',
    prompt: `Read docs/plan/readiness-audit-2026-09/judges/reads.json IN FULL — all twelve reads, all six
pairwise comparisons, and the agreement block. Then skim the two documents the judges were
comparing: docs/plan/readiness-audit-2026-09/msft1/report.md against
docs/plan/readiness-audit-2026-09/baseline/msft1/note.md, and (at least the opening and the
valuation sections of) azn2/report.md against baseline/azn/note.md.

Your job is the single most important diagnosis in this pass: WHAT, PRECISELY, made three
independent judges prefer a chat answer to a 14,000-word sourced research record, on every
dimension, twice, and again after the fixes landed?

Do not summarise the audit's own summary. Go to the judges' actual sentences. For each
distinct reason, count how many of the eighteen judge-readings and six comparisons it
explains, quote the sharpest sentence, and identify what in the PRODUCT (not the prose style)
causes it. Rank the reasons by explanatory power, and be explicit about which are about
(a) the evidence policy, (b) the absence of a stated view, (c) missing figures,
(d) how the document is written, (e) the reading experience itself.

Each item must say what would have to change for that judge sentence to come out differently,
and — this is the acceptance criterion — how we would test that it did.`,
  },
  {
    key: 'view',
    title: 'The report states no view, and says so in its header',
    prompt: `The judges' first reason is that the report's header says "Non-binding view: no view reached",
so there is no thesis to act on. Find out exactly why, and what it would take to change.

Read: src/aer/calc/outcomes.py, src/aer/render/glance.py, the section definitions and the
drafting path in src/aer/sections/, and search the codebase and docs/adr/ for the rating,
verdict, view and recommendation machinery (ADR 0087 introduced a verdict role; ADR 0102
defines a thesis as premises; the skill invariant forbids a skill file setting a rating).
Establish, with file:line:
 - Where the "no view reached" state is decided, and what would have to be true for a view to
   be reached instead.
 - Whether the platform is structurally CAPABLE of stating an argued view within its
   invariants (the model may interpret; only code may state a figure), or whether that needs
   a new ADR and a new surface.
 - What a compliant "view" looks like for a personal research tool that is not investment
   advice: a rating? a valuation range against the price? an explicit list of what would have
   to be true? Give the real options with their risks.

This is the most consequential item in the whole plan. Be concrete about the smallest change
that would let a judge say "yes, a view is stated and argued" without breaking an invariant.`,
  },
  {
    key: 'evidence-policy',
    title: 'The evidence rules exclude what would answer the brief',
    prompt: `The judges said the focus questions are answered "partly" or "no" because the evidence rules
exclude what would answer them — patent-expiry exposure, product-level revenue, the substance
of a recent 8-K, anything not in the filings the run is allowed to read.

Read the evidence tier system end to end: the section definitions and their tier ceilings
(the audit's F-21 item 12 and use case 6 say every built-in section's ceiling is T4, so the
T5 documents a run admits are cited nowhere), src/aer/sections/, the web search path
(ADR 0092: the web is searched as a listing and read only through the gate it already has),
ADR 0111 on undated sources, and the research steps in
src/aer/workflows/vertical_slice_v1.py (research_industry, research_macro and siblings).

Establish with file:line: what a run actually fetches, what it is allowed to cite, where the
ceiling is enforced, and what happens to a T5 document today. Then give the options for
letting news and secondary analysis reach a named section without weakening invariant 1 or 8
(untrusted content is data, never instruction), including what the citation and hashing story
is for a web page, and what the reader is shown about the tier of each claim.`,
  },
  {
    key: 'comps',
    title: 'No peer multiple on any run, ever',
    prompt: `No run in the audit produced a single peer multiple: eight peers are proposed, the operator
confirms them, and every peer is excluded with "this research holds no filings and no price
series for it, and a peer multiple needs both". EV/EBITDA and P/E are therefore withheld from
every report, and market capitalisation was withheld on three of five runs.

Read src/aer/services/comps.py, the comps gate and its payload, the price feed path (EODHD)
in src/aer/sources/ and src/aer/services/, ADR 0059 as amended, and the audit \u00a73.4 and \u00a78 item 1.

Establish: exactly what a peer multiple needs; what the platform already has for a peer (a
CIK? a ticker? nothing?); what acquiring eight peers' companyfacts from EDGAR would cost in
time, storage, run length and money; whether the price feed can price eight peers within the
run's budget; and what point-in-time does to a peer's figures. Give the three options the
audit named (acquire them / drop the multiples from the spine / keep proposing peers and say
the table is structurally empty) with a real cost for each, and say which you would take and
why. Also cover market capitalisation: why was it withheld, and is that the same root cause?`,
  },
  {
    key: 'segments-guidance',
    title: 'No segment revenue, no guidance, no product-level detail',
    prompt: `Both the checklist scoring and the judges put segment revenue for the latest year, management
guidance and named competitors among the six things the console has and the platform does not.
Yet the platform extracts 18,610 facts on Microsoft and writes a Segment Analysis section.

Find out why a segment section exists but segment revenue does not reach the reader. Read the
extraction path: src/aer/services/filings.py, the concept map and its aliases, the unmapped
concepts gate (496 unmapped tags on MSFT, 322 on AZN, 852 on the bank), src/aer/services/facts.py,
and the section that is supposed to carry segments. Establish with file:line whether the
problem is extraction (the dimensional/axis facts are never read), mapping (they are read and
unmapped), selection (mapped but never chosen for a section), or drafting (available and not
used). The answer determines the fix, so do not guess — find the evidence in the code and in a
run export under docs/plan/readiness-audit-2026-09/msft1/.

Do the same for management guidance (is it in the 10-K at all? an 8-K? a transcript?) and for
named competitors (the 10-K's competition section is prose the platform admits — why does no
named competitor reach the report?).`,
  },
  {
    key: 'bank',
    title: 'A bank cannot produce an approvable report',
    prompt: `F-24 is the audit's one open blocking finding: M&T reports no total-revenue caption after
FY2023, so revenue resolves to ASC 606 fee income of $1,657m against net income of $2,851m,
eleven relations among the figures are impossible, and the plausibility guard refuses the
report. F-26 is its neighbour: the sector gate can only be approved or refused, so an operator
who thinks the classification is wrong has nowhere to say so.

Read the audit's F-23, F-24 and F-26 in full; then src/aer/services/sectors.py, the ratio
inputs and the revenue resolution path in src/aer/calc/ and src/aer/services/, the residual
income model, and the plausibility metric in src/aer/eval/.

Deliver: the exact change for each of the three options the audit named for a bank's revenue
(derive it as net interest income + non-interest income for a confirmed bank; withhold it;
leave it), naming the function, the call site and the ADR each needs; the sector gate's third
answer; and whether anything else about a bank's report is unusable once revenue is right —
read docs/plan/readiness-audit-2026-09/mtb/report.md and say whether a residual-income
valuation is legible to a private investor.`,
  },
  {
    key: 'uk',
    title: 'A domestic UK filer cannot be researched at all',
    prompt: `The product's documents claim "UK or US" but acquisition resolves every subject against EDGAR's
ticker list, so a UK company with no SEC filings cannot get past acquisition (F-04). A
Companies House client exists and is credential-wired, and no workflow step calls it.

Read src/aer/sources/uk/companies_house.py, the acquire step in
src/aer/workflows/vertical_slice_v1.py, src/aer/sources/sec/client.py, the runtime wiring, and
the roadmap's \u00a73 for anything already planned here. Establish what actually exists, what a UK
acquisition path needs (company number resolution, iXBRL accounts, the taxonomy difference,
LSE prices, a GBP risk-free rate, a UK point-in-time story), what the concept map would need,
and what would be different for a reader. Size it honestly — this is a new source adapter and
an ADR, not a bug fix — and say what the cheapest honest interim is (for example, refusing
clearly and naming the 20-F route where one exists).`,
  },
  {
    key: 'journey',
    title: 'The operator journey: gates, dead ends and the terminal',
    prompt: `This is the core of "not user ready". Walk the operator's path as the audit recorded it and
find every place it dead-ends, demands too much, or needs a terminal.

Read the audit \u00a74.2, \u00a74.3 (use cases 9, 10, 11), F-08, F-15, F-16, F-22, F-25, and F-21's
items 10 and 11. Then read the code: src/aer/services/approvals.py, gates.py, assumption_gate.py,
resume.py, the web routes and templates under src/aer/web/, and the gate payload builders in
src/aer/workflows/vertical_slice_v1.py. Look at the screenshots in
docs/plan/readiness-audit-2026-09/msft1/screens/ if they help.

Produce the complete list of journey defects with their root causes, including at least:
a run refused at gate 2 that can be re-measured nowhere (F-22); a stale approval with no exit
(F-16); the unmapped-concepts page carrying 105 items on Microsoft and 304 on a bank; the
assumptions page demanding two to five sourced values with a justification each; the promised
citation override that exists on no surface; anything that still needs the CLI; and the
vocabulary that reaches a reader who did not build it. For each, say what the operator should
see instead, and what the smallest change is that gets them there.`,
  },
  {
    key: 'speed',
    title: 'Nothing useful exists at ten minutes',
    prompt: `Use case 7 is "not supported": the console answered the same brief in 14 to 19 minutes with one
prompt; the platform needs 31 to 42 minutes of step time plus six human decisions, and nothing
useful exists at ten minutes.

Read the per-step timings in the run records (docs/plan/readiness-audit-2026-09/*/steps.json or
the run export), the audit \u00a74.2, and the workflow's step graph in
src/aer/workflows/vertical_slice_v1.py — particularly what is serial that could be concurrent,
the research wave, the four-wide draft fan-out, and where the gates sit in the elapsed time.

Establish where the time actually goes, step by step, with numbers from the records. Then give
the options for a faster path: a shorter spine; a "first read" mode that produces the front
page and three sections in ten minutes and continues in the background; fewer gates for a
subject already researched; batching the operator's decisions into one sitting; raising
concurrency. Be honest about which of these trade away something the platform exists for, and
which are free.`,
  },
  {
    key: 'redteam',
    title: 'The adversary\u2019s accusations reach the reader unresolved',
    prompt: `F-27 found that the red team was shown the oldest year's figures with no period label, so it
accused the draft of contradicting the record on every headline figure; the fix landed, but
the judges reading AZN #2 still found the report "carries eight unresolved red-team accusations
against its own figures".

Read F-27 in full, src/aer/services/red_team.py, src/aer/agents/red_team.py, the disagreements
surface in the report (src/aer/render/ and the appendix), ADR 0095 (an escalated challenge is
briefed, not decided) and ADR 0091 (the critic gets a second draft). Then read the
"Validation & Disagreements" section of docs/plan/readiness-audit-2026-09/azn2/report.md and
judge it as a reader would.

Establish: what happens to a challenge that is neither dismissed nor resolved; what the reader
is shown; whether an unresolved accusation against a figure the platform itself computed is
ever legitimate; and what the fix is — resolve them before publication, present them
differently, or stop raising challenges the platform can settle in code. Say what a sceptic
should see and what an investor should see, because they are not the same.`,
  },
  {
    key: 'prose',
    title: 'The document recites rather than argues, and takes 45 minutes to read',
    prompt: `Five of six judges said the platform's report is not argued but recited, and the reading time
is 30 to 45 minutes for 12,500 to 16,000 words against the console's 50 to 60 minutes for a
shorter note that argues. On Microsoft, three of three judges said the numbers are NOT
explained; on AstraZeneca after the fix, three of three said they are.

Read the drafting machinery: src/aer/sections/writing.py, the section definitions and their
word budgets, the agents under src/aer/agents/ that draft and revise, ADR 0091's critique
loop, and the evidence a section is given (how facts, calculations and excerpts are handed to
the writer). Then read three sections of msft1/report.md and the same three of azn2/report.md
and say precisely what is different, since the same machinery wrote both.

Diagnose why the prose recites: is it the word budget, the evidence bundle, the prompt, the
critique rubric, the refusal rules that strip hedged sentences, or the absence of a conclusion
to argue toward? Give the options, and be careful — this is the area where it is easiest to
propose something that violates the one rule. Nothing you propose may let the model state a
figure the code did not produce.`,
  },
  {
    key: 'integrity-cost',
    title: 'The hygiene items: metering, the chain, the suite, the docs',
    prompt: `Gather the smaller "not user-ready" defects the audit left open or noted, size each, and say
which actually matter to a user and which are internal hygiene.

Read the audit's F-01 to F-07, F-13, F-21 (all items) and \u00a76, then the code for each:
 - F-06: no non-model acquisition spend reaches the pounds ledger (the data_api category is
   never written).
 - F-07: the audit chain is application-writable; UPDATE/DELETE were never revoked.
 - F-03: the suite's answer depends on file order, more widely than the recorded pair.
 - F-05: four roles ask for low effort on a model with no effort parameter.
 - F-21's cost items: sibling calls in a wave cannot see each other's spend; a step that dies
   mid-flight loses the rows it flushed; schema-rejected replies cost up to 18,706 output
   tokens a run and no surface sums them.
 - F-21's guard items: a fact-backed numeric claim is never checked against its fact's value,
   and the reading ladder discards the scale word so "$331.8 million" reads as the right
   number.
 - F-01/F-02: CI and the documentation drift.
For each, say plainly whether a user would ever notice, and what it costs to close.`,
  },
  {
    key: 'roadmap',
    title: 'What the roadmap already plans, and what must not be pulled forward',
    prompt: `Read docs/plan/ROADMAP.md IN FULL (1,199 lines — \u00a72 fixes and bugs, \u00a73 new additions, \u00a74
archived, \u00a75 how to work on this), docs/plan/remaining-work.md, and the list of ADRs in
docs/adr/. The repository's rule is that the roadmap is the authority on scope, items build on
each other, and a later item's work must not be folded into an earlier one.

Produce the map between the roadmap and the two issues:
 - Which open roadmap items, if built, would close part of "not user ready" or "not better
   than the console"? Name them by section number with their titles.
 - Which of the audit's findings and \u00a78 decisions are NOT in the roadmap at all and would have
   to be added as new items?
 - What does the roadmap consider next, and would the plan proposed here jump the queue? Where
   it would, say what the justification is or that there is none.
 - Which roadmap items are irrelevant to these two issues and should stay where they are?
Your items should be "add X to \u00a72 / \u00a73", "bring Y forward because Z", or "leave W alone".`,
  },
  {
    key: 'measure',
    title: 'How we would prove either issue is fixed',
    prompt: `A plan that cannot be measured is a wish. Design the measurement, reusing what already exists.

Read the audit harness: audit/README.md if present, audit/subjects.py, audit/driver/,
audit/baseline/, audit/scoring/ (facts, numerals, match, platform_report, baseline_report,
variance, scorecard), audit/ledger.py, and the Workflow-driven judging described in the audit
\u00a74.1 and its results in docs/plan/readiness-audit-2026-09/judges/reads.json. Read \u00a72's
"Deviations from the plan" and \u00a79 "What this pass does not establish".

Deliver:
 - The cheapest credible re-measurement of ISSUE 2: which subjects, how many platform runs,
   how many console baselines (they cost \u00a37 to \u00a311 each), how many judges, and what it costs
   in pounds. Say whether existing baselines can be reused rather than re-bought.
 - The measurement of ISSUE 1, which is not a judge's opinion: a journey harness. What would it
   assert — gate stops, dead ends met, terminal escapes needed, values typed, minutes to a
   readable result — and can the existing Playwright sweep carry it?
 - The target, stated before the work: what result would let the operator say each issue is
   fixed. Be specific and unflattering (for example: at least four of six blind comparisons
   choose the platform, or all three focus questions answered "yes" by two of three judges).
 - Which audit scorers deserve to become permanent run-time metrics (the audit's \u00a78 item 15),
   and what it costs to run them on every report.
 - An honest statement of what a re-measurement still would not establish.`,
  },
]

phase('Diagnose')
log(`Reading ${AREAS.length} areas of the audit record and the code, each with an adversarial second pass.`)

const diagnosed = await pipeline(
  AREAS,
  (area) => agent(`${CONTEXT}\n\nYOUR AREA: ${area.title}\n\n${area.prompt}\n\nReturn the structured diagnosis. Be exhaustive within your area and rigorous about evidence; another agent will try to refute everything you say.`,
    { label: `diagnose:${area.key}`, phase: 'Diagnose', schema: DIAG_SCHEMA }),
  (diag, area) => {
    if (!diag) return null
    return agent(`${CONTEXT}\n\nYOU ARE THE REFUTER for the area "${area.title}".\n\nAnother agent produced this diagnosis. Your job is to attack it, not to agree with it. Default to "weakened" or "refuted" when you cannot verify a claim yourself.\n\nDIAGNOSIS:\n${JSON.stringify(diag, null, 1)}\n\nFor each item:\n 1. Open the files it names and check the root cause is actually what it says. A root cause that is not in the code where the item claims it is is REFUTED.\n 2. Check the recommendation against CLAUDE.md's invariants and against docs/adr/. If it would breach one without a new ADR, say which, in invariantRisk.\n 3. Check the effort estimate. Anything wrong by more than about two times is WEAKENED with a correction.\n 4. Check the acceptance criterion is actually measurable on a run or in the suite. "The report reads better" is not measurable.\n 5. Check the evidence references resolve — the audit section exists and says that, the file:line exists.\nThen say what this area's reader did not look at, as actionable items in "missed".`,
      { label: `challenge:${area.key}`, phase: 'Challenge', schema: CHALLENGE_SCHEMA, effort: 'high' })
      .then((ch) => ({ area: area.key, title: area.title, diagnosis: diag, challenge: ch }))
  },
)

const areas = diagnosed.filter(Boolean)
const allItems = areas.flatMap((a) => (a.diagnosis.items || []).map((it) => {
  const v = ((a.challenge && a.challenge.verdicts) || []).find((x) => x.id === it.id)
  return Object.assign({}, it, {
    area: a.area,
    verdict: v ? v.verdict : 'unchecked',
    challengeNote: v ? (v.correction || v.why) : '',
    invariantRisk: v ? (v.invariantRisk || '') : '',
  })
}))
const surviving = allItems.filter((it) => it.verdict !== 'refuted')
const refuted = allItems.filter((it) => it.verdict === 'refuted')
const missed = areas.flatMap((a) => ((a.challenge && a.challenge.missed) || []).map((m) => `[${a.area}] ${m}`))
log(`${allItems.length} items diagnosed; ${surviving.length} survived the refuters, ${refuted.length} refuted; ${missed.length} gaps named by refuters.`)

const BRIEF = JSON.stringify({
  items: surviving.map((it) => ({
    id: `${it.area}/${it.id}`, title: it.title, issue: it.whichIssue, impact: it.impact,
    problem: it.problem, rootCause: it.rootCause, recommendation: it.recommendation,
    options: (it.options || []).map((o) => o.name + ': ' + o.change),
    files: it.filesTouched, adr: it.adrNeeded ? it.adrSubject : '', tests: it.tests,
    acceptance: it.acceptance, effort: it.effort, dependsOn: it.dependsOn || [],
    operatorDecision: it.operatorDecision, verdict: it.verdict, note: it.challengeNote,
    invariantRisk: it.invariantRisk,
  })),
  refutedAndWhy: refuted.map((it) => `${it.area}/${it.id}: ${it.challengeNote}`),
  gapsNamedByRefuters: missed,
  areaSummaries: areas.map((a) => `${a.area}: ${a.diagnosis.summary}`),
  openQuestions: areas.flatMap((a) => (a.diagnosis.openQuestions || []).map((q) => `[${a.area}] ${q}`)),
}, null, 1)

phase('Plan')
const FRAMINGS = [
  { key: 'document-first', steer: `Organise the plan around ONE idea: the document must become worth reading before anything else is worth doing. The platform already produces an unimpeachable evidence base that nobody wants to read. Sequence the view, the evidence policy, the missing figures and the prose first, and treat journey defects as what you fix while live runs are in flight. Argue why the user-ready work can wait behind this.` },
  { key: 'journey-first', steer: `Organise the plan around ONE idea: a tool that dead-ends is not a tool, and the operator must be able to finish and recover a run before a better document matters. Sequence the blocking defects, the dead ends, the bank, the gates and the recovery paths first; treat the document work as the second half. Argue why fixing the document first would be building on sand.` },
  { key: 'measurement-first', steer: `Organise the plan around ONE idea: the audit cost £63 and five weeks of context to produce a verdict, and the platform cannot improve what it cannot re-measure cheaply. Sequence a permanent measurement harness first — journey assertions, the scorers as run-time metrics, a repeatable blind judge panel with reusable baselines — then fix in measured increments, each proven by a re-run. Argue why this pays for itself, and be precise about what it costs before any product improvement lands.` },
  { key: 'open', steer: `Ignore the three framings the other planners were given. Decide for yourself what the right shape of this plan is, including the possibility that the two issues are really one issue, or that the right move is to narrow what the product claims to be rather than to build everything. If you think the honest answer is that some part of the gap should not be closed at all, say so and say what the product is then for.` },
]

const plans = (await parallel(FRAMINGS.map((f) => () => agent(
  `${CONTEXT}\n\nYou are one of four independent planners. Each of you sees the same verified diagnosis and writes a COMPLETE plan of action; a judge panel will then score all four and one plan will be synthesised from them. Write the best plan you can, not a compromise.\n\nYOUR FRAMING: ${f.key}\n${f.steer}\n\nTHE VERIFIED DIAGNOSIS (items that survived adversarial refutation, plus what was refuted and what the refuters said was missed):\n${BRIEF}\n\nRequirements:\n - Every workstream and step must trace to diagnosis ids. You may add items the diagnosis missed; mark them "NEW:".\n - Respect the invariants and the ADR process; name every new ADR the plan needs.\n - The running order must be executable by one person in AI coding sessions, and must say which steps need live runs, on which subjects, and what they cost.\n - The measurement section is not optional and must state the target BEFORE the work: what result would let the operator say each issue is fixed.\n - Say what you are deliberately not doing.\n - Be honest about anything that cannot be fixed without changing what the product claims to be.\n\nRead any file you need to before writing; do not modify anything.`,
  { label: `plan:${f.key}`, phase: 'Plan', schema: PLAN_SCHEMA, effort: 'high' },
)))).filter(Boolean)
log(`${plans.length} plans written. Scoring.`)

phase('Score')
const LENSES = [
  { key: 'operator', steer: `You are the operator: a private investor who paid for this, runs it on one machine, has a Claude subscription as the alternative, and wants to use the thing in anger within weeks. Score for what actually reaches you and when.` },
  { key: 'engineer', steer: `You are the engineer who will execute this in AI coding sessions on this repository. Score for whether each step is actually specified enough to start, whether the sequencing respects real dependencies in the code, whether the tests named would catch regressions, and whether the effort estimates survive contact with this codebase.` },
  { key: 'sceptic', steer: `You are the sceptic who wrote the audit. Score for evidence: does each claim trace to something measured? Is any step justified by a hope? Would the measurement proposed actually detect failure, or is it designed to pass? Punish plans that would let the operator declare victory without evidence.` },
]

const scored = (await parallel(LENSES.map((l) => () => agent(
  `${CONTEXT}\n\nYou are judging four independent plans for fixing the two issues. Your lens: ${l.key}.\n${l.steer}\n\nScore each plan 1-5 on: evidence (each step traces to a measured finding), invariants (respects CLAUDE.md and the ADR process), sequencing (the order is right and dependencies hold), measurability (the targets would actually detect failure), realism (effort and cost survive contact with this repository), coverage (both issues, nothing decisive missed). Total is the sum.\n\nName any fatal flaw. Say what you would graft from each plan into the winner. Then rank them and give synthesis advice: which plan is the spine, and exactly what to take from the others.\n\nTHE PLANS:\n${JSON.stringify(plans, null, 1)}`,
  { label: `judge:${l.key}`, phase: 'Score', schema: SCORE_SCHEMA, effort: 'high' },
)))).filter(Boolean)

phase('Critique')
const critics = (await parallel([
  `You are a completeness critic. Read the diagnosis and all four plans and answer one question: WHAT IS MISSING? Consider — a subsystem nobody diagnosed; a use case in the audit's suitability matrix that no plan touches; a failure mode the plans would introduce; a user reality nobody accounted for (Windows, backups, key rotation, a second company, what happens after the report is written); an operator decision nobody surfaced; a cheap high-impact fix nobody proposed. Be specific and actionable, and do not repeat what the plans already cover.`,
  `You are an adversarial critic whose job is to find the ways this plan could take months and still lose the blind comparison. Attack the shared assumptions: that a stated view is what the judges want; that peer multiples matter to a private investor; that the evidence policy is what stands between the report and usefulness; that measurement is cheap; that the console will stand still. For each assumption the plans share, say what the audit record actually supports and what it does not, and what the plan should do differently if the assumption is wrong.`,
].map((p) => () => agent(
  `${CONTEXT}\n\n${p}\n\nTHE DIAGNOSIS:\n${BRIEF}\n\nTHE PLANS:\n${JSON.stringify(plans, null, 1)}`,
  { label: 'critic', phase: 'Critique', schema: CRITIC_SCHEMA, effort: 'high' },
)))).filter(Boolean)

return {
  counts: { areas: areas.length, items: allItems.length, surviving: surviving.length, refuted: refuted.length, plans: plans.length },
  areaSummaries: areas.map((a) => ({ area: a.area, summary: a.diagnosis.summary, openQuestions: a.diagnosis.openQuestions || [] })),
  items: surviving,
  refuted: refuted.map((it) => ({ id: `${it.area}/${it.id}`, title: it.title, why: it.challengeNote })),
  gapsNamedByRefuters: missed,
  plans,
  scores: scored,
  critics,
}
