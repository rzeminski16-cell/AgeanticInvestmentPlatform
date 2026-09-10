# ADR 0110 — A run is dated by the platform, not by the operator

**Status.** Accepted
**Date.** 2026-09-09
**Amends.** ADR 0107 (a watchlist is followed continuously and researched as at a date) —
the phrase stands and now has one meaning. ADR 0010 (point-in-time is selection, not
filtering) and ADR 0021 (look-ahead is checked twice) are untouched: every guard still reads
a date, and the date is still real.
**Does not touch.** ADR 0075 (the portfolio clock is not the research clock) or the
post-trade reviewer's own as-of date. Those are dates *about something*, not choices.
**Required by.** The first full manual acceptance pass
(`docs/plan/acceptance-2026-09-08.md` §5), where the operator asked for the as-of date to
stop being something they set.

## Context

Every research request carried an `as_of_date` the operator typed, defaulting to nothing,
refused if it was in the future, and used to select filings, bound facts, bound a peer's
periods, judge look-ahead and label the report. It is the platform's whole temporal
apparatus, and none of that is in question here. The question is narrower: **should a person
choose it?**

The capability it buys is commissioning research about a past quarter — the report you would
have written on 31 March, written now, without the hindsight of what happened since. That is
a real and valuable thing, and it is not what this platform is for. It produces one
institutional-style research report at a time about a company as it stands. The operator's
own words in the acceptance notes are that the date should stop being something they set.

What it costs is subtler than one field on a form. A date somebody types is a date somebody
can get wrong, and a wrong one fails **silently** — ADR 0010's own argument, in reverse. Set
it a week early and the newest 10-Q vanishes with no error anywhere; set it to a quarter end
out of habit and the run reads a company that no longer exists, still citing everything
correctly, still passing every check. The report is wrong in the one way this platform is
built to make impossible, and the only evidence is a date in a header nobody re-reads.

The measurement in the acceptance document is what makes this a decision rather than a
rewrite: `as_of_date` appears 527 times across 86 source files, and the sites where it
*changes behaviour* number about fifteen. This ADR touches none of them.

## Decision

**`work_orders.as_of_date` is a stamp, written by the platform when a run is commissioned.**

- `ResearchRequestCreate` loses the field. The model is `extra="forbid"`, so an API client
  that still sends one is told, rather than having it dropped: a client that had been
  commissioning research about a past quarter should not keep believing it is.
- `create_request` writes `limits.today`. That is the same `RequestLimits` the rules are
  checked against, so a request cannot be judged on one day and dated to another by a clock
  read that crossed midnight between the two.
- The request form states the date instead of asking for it, above the point-in-time choice
  it governs. An operator who cannot see the date cannot tell what that choice applies to.
- `_apply` no longer writes it, and `_EDITABLE_FIELDS` no longer names it. It is stamped
  once and never edited — which is what the freeze on a live request already enforced, for
  the reason it gave: *move the as-of date and evidence that was admissible is not.*
- The watchlist commissions as at the day the queue reaches the entry. That is what ADR
  0107's "researched as at a date" has always meant in practice, and the commission row
  keeps recording the date, because *when* an entry was researched is exactly what a list of
  past commissions is for.
- `check_limits` loses the future-date rule. Not relaxed — the field it guarded is gone, and
  the stamp cannot be in the future because it *is* the clock.

**Nothing downstream changes**, and that is the point of the sequencing. "The latest filing
on or before today" is "the latest filing". `select_point_in_time` still selects. Every
adapter still bounds. Both look-ahead checks still run against a date they can still read.
Replaying an old run still works, because a replay reads that run's own stamp.

## What is given up, named rather than discovered

**Commissioning a new run about a past quarter.** There is no way to ask for one. An
existing run dated to a past quarter keeps its date, its evidence and its report, and can be
re-read and replayed; what cannot be created is a new one. If that capability is ever wanted
back it returns as a deliberate mode with its own name and its own warnings, not as a date
field that is wrong by default.

**The point-in-time choice stays**, and it now means something slightly different, so the
form says so. With the date fixed at today it no longer trades hindsight against fidelity;
it decides whether to admit a document whose own evidence puts it in the future, which is a
document that has been mis-dated or made up. That is a narrow and occasionally useful thing
to allow — better than overriding such documents one at a time — and it is still the
operator's call.

**The second look-ahead check keeps its job, on a smaller argument.** `aer.verify.citations`
justified itself partly on "a document cited after an operator has moved the as-of date
earlier", and nobody can move it now. What that removes is one way in, not the mismatch: a
run commissioned on Monday and still acquiring on Tuesday is dated Monday, and acquisition
still cannot know what a claim will later rest on. The module docstring says the smaller
thing rather than continuing to cite a capability that no longer exists.

## Consequences

**One clock, read once per request.** `limits_from` is where it is read, and both the
validation and the stamp come from that single value. The UTC-date decision it documents is
unchanged and now has one visible effect rather than two: shortly after local midnight in a
positive-offset timezone a run is dated to what is already tomorrow locally. Every surface
prints the stamp, so it is a fact the operator can see rather than a rule they must know.

**A whole class of silent error is gone.** Not caught — *unavailable*. The date cannot be a
week early, a quarter old, or last year's, because nobody types it.

**The form is shorter by one required field**, and the sheet is now called *Hindsight*
rather than *Date and hindsight*, because the date is no longer a question.

## Alternatives considered

**Default it to today and leave it editable.** The cheapest change, and it keeps the failure
mode exactly as it is: the field that is wrong is still the field somebody can set. A default
that is right is not a rule.

**Keep it for the API and remove it from the form.** Two behaviours for one platform, and
the API is the surface with no operator reading the page to notice. If the capability is
worth having it is worth having deliberately on both.

**Remove `point_in_time` as well.** Tempting, since with the date fixed at today the choice
is nearly always the same one. But "nearly always" is not "always" — an extractor that reads
a date wrongly is a real thing, and refusing an operator the ability to admit the document is
narrowing their tool for tidiness. It stays, with copy that says what it now does.

**Drop the column.** It is the run's own record of what it was judged against, read by both
look-ahead checks, by every adapter's selection, by a replay and by the report's header. A
stamp is not a redundant input.
