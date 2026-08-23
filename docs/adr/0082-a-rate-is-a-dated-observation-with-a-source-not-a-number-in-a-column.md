# ADR 0082 — A rate is a dated observation with a source, not a number in a column

**Status.** Accepted
**Amended by.** ADR 0084, in one column: ``fx_rates.source_document_id`` is nullable with
``ON DELETE SET NULL``, and the guarantee this record wanted from its ``NOT NULL`` moved to a
``NOT NULL`` ``artefact_sha256``. Nothing else below is changed. The reasoning is left
exactly as written — ADR 0001 forbids editing it, and it is right about everything except
what a purge does to a request-scoped pointer.
**Date.** 2026-08-22
**Required by.** ADR 0073, ADR 0080 and ADR 0081 — each of them needs a currency converted,
and none of them can decide the rate store without becoming a record about something else.
**Extends.** ADR 0045, which made the euro the pivot, and ADR 0026, which shipped the
arithmetic and deliberately shipped no source.

## Context

**`aer/calc/fx.py` is finished and nothing calls it.** Rate selection with a look-ahead
refusal and a seven-day staleness limit; a traced `convert` recording the rate as an input;
an `invert` that keeps the original's source, because an inverted rate is one observation
read backwards rather than a second piece of evidence; a `cross` that divides two published
legs — and the module is registered for replay (`eval/replay.py:67`). `ecb.py` records the
position in its own module docstring without softening it: `aer.calc.fx` "shipped complete
and was never given a source: every rate had to be supplied by hand".

**Half the plumbing then arrived and stopped.** ADR 0045 admitted the ECB Data Portal,
`ecb.py` parses SDMX-CSV into `FxRate` values, and `MacroClient.fetch_reference_rates`
(`sources/macro/client.py:207`) fetches and parses one currency's daily series. **It has no
callers**, and nothing in `src/` calls `select_rate`, `convert_at` or `cross`: the only
`import` statements naming it are in the two adapters that construct its objects for nobody.

**There is no `fx_rates` table.** The nearest thing is `costs.fx_rate`
(`db/models/cost.py:70`), a `Numeric(12, 6)` holding the USD→GBP rate applied to one model
call's spend (`providers/costs.py:176`). That is a metering field on a billing row: one pair,
no date but the call's. Reading it as a rate store is reading a receipt as a price list.

**So a multi-currency NAV is not merely unbuilt. It is uncomputable, and correctly so.**
`Quantity.__add__` (`calc/units.py:423`) goes through `_require_same_unit`, which refuses
dollars and pounds and says why — "Currencies never convert implicitly — use convert() with a
sourced rate". `Quantity.convert` (`units.py:488`) then raises `UnsourcedValueError` on a rate
carrying no `SourceRef`, and `FxRate.__post_init__` refuses one before it gets there. Three
refusals in a row, each deliberate: the type system is declining to produce a figure the
platform has no evidence for. **That refusal is the feature, and this record does not relax
it.** It gives it something to say yes to.

## Decision

**An `fx_rates` table shaped like `macro_observations`, and a `services/fx.py` shaped like
`services/macro.py`, calling the client that already exists.**

A row is keyed `(pair, observed_on, vintage)` and carries the document it came from.

* **`observed_on`** is the day the rate was *for*, which `FxRate` already keeps distinct
  from the day it was fetched, and the field `select_rate` filters against the as-of date.
* **`vintage`** is the day the platform's reading of that publication was as at. The ECB
  publishes a rate once and corrects rarely, which argues for the column rather than against
  it: a correction applied as an `UPDATE` would silently rewrite an input to arithmetic that
  has already run and possibly already been approved. It adds a row instead, as a GDP revision
  does, under `macro_observations`'s unchanged rule that a vintage cannot precede its period.
* **The source document is `NOT NULL`**, which is where this table departs from the one it
  copies. `macro_observations.source_document_id` is nullable so a fixture-loaded row can
  exist without a fetch behind it; on a rate, that nullability *is* the hand-typed number this
  record exists to displace. Such a value is not an `fx_rates` row — it is the attestation two
  sections below.

`services/fx.py` mirrors its model: a recorder idempotent on the key, so a retried
acquisition writes no second copy and makes no revision appear where none happened, and a
reader answering "what could somebody on the as-of date have seen?" that returns nothing
when the answer is nothing. **It does not fall back to the newest row it can find** — the
fallback `services/macro.py` refuses, invisible in the output, because a rate looks like a
rate whichever week it was published in.

### Every non-euro pair is a derived cross, and this record prices that

ADR 0045's consequence is carried forward unaltered: the ECB publishes the rates *of the
euro*, so a GBP/USD rate does not exist at that source and never will. `aer.calc.fx.cross` is
where the division happens, a `@traced` calculation rather than a helper, so a reader
following a converted figure back reaches two source documents and a formula rather than a
number that looks published.

**At portfolio volume that shape has a price, and it is not the shape that is new.** One NAV
on a twenty-position multi-currency book is roughly twenty conversions, each resolving two
stored legs and one traced cross. ADR 0028 accepted arithmetic of this density already — a
nine-by-nine sensitivity grid is eighty-one complete valuations and about six thousand
`calculations` rows, taken over interpolation because "the numbers are real" is exactly the
claim a reader cannot check. **What is new is the cadence.** A grid runs when somebody asks
for a valuation; a NAV runs every day the book is open, whether or not anybody reads it. This
is the first time the platform accepts that shape *daily*, and that is what this record
admits rather than discovers.

## The rate is not a price, and the store must not be asked to pretend otherwise

The ECB's own words, already carried in `ECB_LICENCE` and in `ecb.py`'s docstring: the
reference rates are indicative and "not intended to be used in any market transaction". That
is not a disclaimer to inherit quietly — it decides what this store is for, and the decision
is made here rather than left to whoever writes the first caller.

**The rate store serves translation, not marking.** Restating a sterling-reporting balance
sheet in dollars, or a book of positions into one reporting currency, is exactly what a daily
reference rate is for. A figure that is, or stands in for, a transaction price — an execution,
a fill, a gain crystallised at a rate somebody actually dealt at — needs its own source, and
this store does not supply it. Nothing in `ecb.py` offers a bid, an ask or an intraday value,
and nothing here may acquire one by being convenient.

**GBX is a separate conversion and stays where it is.** A London listing quoting in pence
carries `quote_currency = "GBX"` on the security (`db/models/security.py`) and reaches major
units through `MINOR_UNITS` in `aer/calc/prices.py`, one traced calculation over a table with
one deliberate entry. That is a quote convention, not a currency pair: no observation, no
publication date, no counterparty, only a factor of one hundred true every day. `GBX/GBP` in a
table of dated observations would invent a rate history for a constant.

## The operator-typed rate, and what it costs

ADR 0073 admits an `attested` Attestation — a value backed by nothing but the operator's word
— and names the unsourceable FX rate as the first thing that will reach for it. The two
records have to agree on how they meet, or the fallback becomes the default by drift.

**An attested rate is admissible, and its grade propagates.** It converts, and the conversion
is still a recorded calculation over a value with a stated origin. What it cannot do is
disappear: ADR 0073's propagation is a return type with no field for the figure, so a NAV three
levels above an attested leg reaches no shareable surface at all. That is the bargain — the
operator can always get an answer, and the answer is marked, structurally, as one the platform
will not stand behind.

**Which makes the fallback expensive in the right way** — not forbidden, not warned about,
costly. A pair inside `REFERENCE_CURRENCIES` that could have been fetched and was typed
instead has traded a shareable NAV for a saved request, and whoever made that trade meets the
consequence on the surface they wanted to share. A currency the ECB does not publish, or a
day the portal was unreachable, is the case the fallback exists for, and the only one.

## Consequences

**A multi-currency NAV becomes computable**, and `aer.calc.fx` acquires its first caller
having shipped complete twice: under ADR 0026 with no source, under ADR 0045 with a source and
nowhere to put it. The module itself does not change; everything it refuses, it goes on
refusing.

**The FX ledger becomes the highest-volume source of calculations in the platform.** ADR 0028
observed that `calculations` was already the largest table by row count and declined to
pretend the rows were free; a cross per pair per book, daily rather than on demand, compounds
that. Retention is not settled here and this record does not claim it is.

**ADR 0031's purge question reaches rate artefacts too.** An ECB response is a fetched
artefact like any other: its bytes are purgeable, and an `fx_rates` row keeps its source
document afterwards, so "where did this rate come from?" survives the purge even though "show
me those bytes" does not — the property `price_bars` already relies on. ADR 0075 raised this
for the marks; it applies unchanged to the rates those marks were translated with. And
`fx_rates` is another leaf table for ADR 0076's resolver registry, arriving after that
registry exists rather than before it, which is the ordering the build sequence chose.

**What this does not fix.** The GBP risk-free rate is still missing and this record must not
be read as having supplied one. `RISK_FREE_SERIES` has no `GBP` entry because the UK proxy is
the ten-year gilt yield, published by the Bank of England, whose `robots.txt` disallows the
CSV handler the Bank documents for programmatic use — ADR 0026's determination, left standing
by ADR 0045. The ECB publishes exchange rates, not yields, and a sterling valuation still
refuses at `risk_free_series_for("GBP")`. That refusal is still correct.
