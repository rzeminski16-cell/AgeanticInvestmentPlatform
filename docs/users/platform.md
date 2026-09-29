# Platform

Where you go about the tool rather than about your money: what it has spent, what you allow
your book, how the watching is set, where the facts come from, whether a copy of it all
exists, and whether anything is running. **Platform** at the foot of the menu opens it.

> **Nothing on this page is a form, and nothing on it is the only way to anything.** Each
> sheet links to the page where its lines are changed — the settings, your book's limits,
> the costs call by call — and a key is shown as *there* or *not there*, never as itself.

---

## The six sheets

**Costs.** What was spent this month against your monthly ceiling, which is enforced in code,
then that spend by the work that incurred it: full reports, refreshes, questions, reviews,
readings of the book. **Discarded** is its own line — replies the model was paid for and
that could not be used, because it answered in a shape the schema refused. Nothing else in
the platform sums it; if it is often more than pennies, something in a prompt or a route is
worth looking at. *Every call, and what the cache saved* opens the costs in detail.

**Your book's limits.** Each limit you have stated, with what it means as the book stands —
*1 position is over it today*, *47.2% today* — and the latest shock you stated on the risk
page. A limit you have not stated reads *not set*; the platform never proposes one
([ADR 0136](../adr/0136-a-limit-on-the-book-is-the-operators-stated-and-never-proposed-and-it-blocks-nothing.md)).
*Set or change a limit* opens Book.

**Monitoring.** The default price threshold every company you follow uses unless you gave
it its own; the default cadence; when the daily checks run (22:00 UTC, after the New York
close) and when they last did; and how many price alerts you dismissed in six months —
too many dismissals mean the threshold is wrong, not the market.

**Data and evidence.** Each source and whether it is ready: SEC EDGAR for US filings,
Companies House for UK filings, EODHD for prices, FRED for rates, and the model. A source
with no key says so, in amber, with what cannot be done without it. Then how many
documents are held, each under the hash of its own bytes.

**Backups.** What the platform knows about its own copies — which today is nothing. A
backup is taken and restored from the terminal (see
[getting-started.md](getting-started.md)), into a folder you choose, and neither writes
anything this page can read. So it says *Not recorded* and *Never recorded*, in amber,
rather than a reassuring line it cannot support. A backup nobody has restored is a hope,
not a backup.

**Health.** The worker, the daily pass, the queue, your last run and the database, each
with its state and the sentence behind it. A worker that has not reported is *Not running*
and nothing will run until one starts; a daily pass more than six hours late is *Overdue*;
a database behind the code names what it is missing.

## What this page does not do

- It changes nothing. Every setting is changed on its own page, one at a time.
- It shows no credential, even masked.
- It does not take or restore a backup.
