# Monitor

What has been filed since you wrote a thesis, read against its premises.

> **A finding is a question, not an answer, and never advice.** The monitor tells you that a
> filing bears on something you believe. It does not tell you what to do, and it never reads
> the share price. What you do about a finding is yours, and the platform records it.

---

## The idea in one line

**Each premise with a threshold is re-read against every new filing, and the one that fails
opens a gate.**

## What a pass does

A pass reads one thesis. For each held premise that names a metric, a comparator, a threshold
and a unit:

1. **Code measures the metric** from the latest fiscal year filed since the premise was last
   read — a growth, a ratio, or a line's level — and decides whether the predicate holds. A
   threshold in per cent is read as a fraction; a threshold in dollars is compared only with a
   line in dollars, and a mismatch is reported rather than guessed at.
2. **The model reads the facts** and says what they do to the premise: *unchanged*,
   *weakened*, *strengthened*. It cannot overrule the measurement: a premise the filing has
   defeated is *contradicted* whatever the model says.
3. **A finding is written**, naming the documents the reading rests on.

A premise nothing new bears on is not read, costs nothing and leaves nothing. A premise the
platform cannot measure — "management allocates capital well", or a segment line the
analysis does not read — leaves an *unobservable* finding saying what it would have
understood. A premise you review by a date is never read by the monitor; it appears under
**Reviews due** when the date passes.

## Running it

Open **Monitor** and press **Run the monitor**. One pass per open thesis is queued on the
worker, so the worker must be running. From a terminal, `uv run aer monitor` runs every pass
in the foreground, and `--thesis ID` runs one; either is what a nightly schedule calls.

A pass spends against the per-run cap and the monthly cap like any run. If a call would
breach either, the pass **stops** and leaves a finding saying so; it never pauses waiting for
an approval, because nobody is there at three in the morning to give one.

## Reading the findings

The list has two groups, and the difference is the whole point:

- **Decisions waiting** — a premise was contradicted. The finding opened a gate. Nothing about
  the premise moves until you decide.
- **Questions raised** — everything else. The monitor noticed something; nobody has yet said
  what they did about it.

Each finding shows **what code measured** — the figure, the threshold, whether the predicate
holds, beside the calculation it came from — apart from **what the monitor read into it**,
which is an interpretation and never evidence.

## Closing a finding

A finding is closed only by you, with a reason. It never disappears because the next pass
found things unchanged.

- **At the gate**: *Withdraw the premise* records that the filing defeated it, with your
  reason on the premise itself; *Keep the premise despite this* records that you saw the
  contradiction and hold the view anyway, with your reason. Both are approvals in the full
  sense — an actor, a hash of what you were shown, an entry on the audit trail.
- **On any other finding**: *Read, and leaving the premise as it is*, or *Withdraw the
  premise*. "I saw this and chose to do nothing" is worth recording.
- **Reopen** a resolved finding if you change your mind, with a reason.

## What this tool does not do

- It does not test a premise against the share price (ADR 0079).
- It does not rate, recommend, size a position or revise a target. Its output has no field
  for any of those.
- It does not decide anything. A contradicted premise raises the question; the answer is yours.
