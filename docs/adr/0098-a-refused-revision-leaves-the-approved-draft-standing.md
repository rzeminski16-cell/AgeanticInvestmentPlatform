# ADR 0098 — A refused revision leaves the approved draft standing

**Status.** Accepted
**Date.** 2026-09-01
**Amends.** ADR 0091, which gave the writer a second attempt against the red team's
material challenges. It did not say what happens when the second attempt is worse than the
first, and the answer the code gave was the wrong one.
**Required by.** Roadmap §2.1, diagnosed from the MSFT run's exported record.

## Context

ADR 0091's loop redrafts up to four challenged sections before a person sees the draft.
`revise_challenged_sections` does it in this order:

```python
await _replace_claims(session, section_id=section.id)
execution = await execute_builtin_section(context, section=section, ...)
```

The claims are deleted first, and `execute_builtin_section` mutates the section row in
place. So when the revision is refused, `_failed` writes `FAILED`, clears the confidence
and leaves `section.content` empty — over a draft that had already passed the full
validation, recorded its claims and been paid for.

**On the MSFT run of 2026-08-31 that cost two sections outright.** Balance Sheet &
Liquidity drafted with 24 recorded claims and Scenarios & Sensitivities with 21; both are
four-byte nulls in the finished run, because their revisions were refused. Six other
sections failed at `draft` — a validation problem, and three of the four causes behind
those are now fixed. These two are not a validation problem. They had *already passed*.

That inverts what the loop is for. ADR 0091 exists to improve a draft, and it is currently
the only mechanism in the platform that can take a section which met every rule and leave
the reader with nothing. A red team remark is enough to lose the section it remarks on.

## Decision

**A revision that does not pass changes nothing about the section.** The approved draft,
its status, its confidence, its stated reason and its claims all stand exactly as the
draft step left them.

**The claim replacement moves to where the new claims are written.**
`record_draft_claims` — which runs only after `validate_draft` came back empty — deletes
the section's existing claims immediately before recording the new ones. On a first draft
there are none and it is a no-op; on a revision it is the replacement ADR 0091 promised,
now performed at the moment there is something to replace them *with*. Nothing else may
delete a section's claims speculatively.

**The row is snapshotted and restored, not rolled back.** `revise_challenged_sections`
holds the four mutable fields before the attempt and writes them back when the execution
returns `FAILED`. A database savepoint would be tidier and is wrong: the `agent_runs` and
`costs` rows from the refused attempt are inside the same session, the money was genuinely
spent, and an audit trail that discards the calls it did not like is not an audit trail.

**The record says the attempt happened and was refused.** A fourth disposition,
`revision_refused`, joins `revised`, `stood` and `skipped_custom` on `revision_notes`
(migration 0063 — the check constraint enumerates them). It travels in the gate-2
payload through `revisions_for_job`, so it is inside the hash: the operator approves
knowing the challenge was answered by an attempt that did not stand up, rather than
silently.

**The kept draft's confidence does not move.** It is the draft that passed, and the
failure of a later attempt to improve it says nothing about it. The unaddressed challenge
is not hidden by this — it is its own disagreement row, shown at gate 2 and in the
appendix, exactly as it would be if the revision had never been attempted.

### What is not weakened

**A revision that passes still replaces everything.** Content, claims, citations,
confidence and reason, as before. **The bound is untouched**: at most four sections, most
severe first, each revised once, custom sections never. **Nothing is retried.** A refused
revision is not a third attempt at the section; it is one attempt that did not stand up.

## Consequences

### Accepted costs

- **A refused revision is money spent for no change.** It always was; what changes is that
  it no longer also costs the section. The `revision_refused` row is what makes the spend
  legible rather than invisible.
- **The gate-2 payload gains a value it could not previously carry.** Runs sealed under an
  earlier build are unaffected in shape — the builder is unchanged — but a run whose
  revision is refused now hashes a payload an older build could not have produced. That is
  the standing property of the seal, not a new one.
- **A section can now reach the report carrying a draft the red team objected to**, where
  before it reached the report as a failure. That is the trade, and it is the right way
  round: a challenged section a reader can read and weigh against the challenge beats a
  blank one, and the challenge is on the same page.

### What this buys

The two sections the MSFT run lost after they had already succeeded, and the standing risk
that any future run loses a good section to an attempt at improving it.
