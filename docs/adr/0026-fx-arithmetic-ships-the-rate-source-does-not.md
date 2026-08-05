# ADR 0026 — FX arithmetic ships; the rate source does not

**Status.** Accepted
**Date.** 2026-08-05
**Supersedes.** Nothing. Extends the practice set by ADR 0022.

## Context

Task 22 needs currency normalisation: a sterling-reporting company and a dollar-reporting
peer are not comparable until something converts one of them. `docs/phase-3-plan.md` names
the source — "Rates from the Bank of England IADB (free, OGL, **subject to the determination
above**)" — and that parenthesis is doing real work. Task 18 also started as an expectation
that a public body's data would be freely fetchable, and ended with the FCA National Storage
Mechanism unfetched, because the licence was not the binding constraint: the terms of use
were.

So the question is two questions, and they have different answers.

## The licence: settled, and permissive

Reproduction of data from the Bank of England's Interactive Statistical Database is under
the UK Open Government Licence. The OGL permits commercial exploitation — "you are free to
exploit the Information commercially and non-commercially, for example by combining it with
other Information, or by including it in your own product or application" — subject to an
attribution statement. That satisfies the standing constraint that this platform only use
sources whose rights extend to future commercial use of the software.

Nothing here is in doubt. The Bank publishes rate series under a licence that allows exactly
what this project would do with them.

## The access route: not settled

The Bank's `robots.txt` disallows, for all user agents:

```
Disallow: /boeapps/database/ShowChart.asp
Disallow: /boeapps/database/_iadb-FromShowColumns.asp
Disallow: /boeapps/iadb
Disallow: /boeapps/titan
```

Two of those four paths are IADB paths. The endpoint that third-party clients use to pull
CSV — the R `boe` package on CRAN, and the commonly cited Python recipes — is
`/boeapps/database/fromshowcolumns.asp?csv.x=yes&SeriesCodes=…`, which under prefix matching
is **not** one of the four. But the sources describing that endpoint do not agree with each
other: at least one describes the CSV endpoint as `_iadb-FromShowColumns.asp`, which **is**
disallowed. Whether the download route this platform would use is permitted therefore turns
on a detail that the available evidence contradicts itself about.

That detail could not be checked. This build environment's network policy blocks outbound
HTTPS to every host — `curl` to `bankofengland.co.uk/robots.txt` returns a 403 from the
proxy, as does every fetch tool — so the `robots.txt` above is corroborated from independent
search results rather than read at source, and neither the Bank's `/legal` terms of use nor
the database Help page could be read at all. In particular, **whether the Bank's website
terms contain an automated-access clause of the kind the FCA's do is unknown**, and that is
precisely the clause that decided task 18.

## Decision

**Ship the arithmetic. Do not ship the fetcher.**

`aer/calc/fx.py` is built and tested: rate selection with the look-ahead refusal, a staleness
limit, a traced conversion that records the rate as an input, and inversion for round trips.
It is pure — it is *given* rates and does not go and get them — so none of it depends on the
unresolved question.

No `Provider.BANK_OF_ENGLAND`, no host in any fetch allowlist, no adapter. A run that needs
a rate today must be given one, and a rate given by hand is a rate with a source like any
other.

The host is **not** added to `REFUSED_HOSTS` either. That list is for sources whose terms
have been read and found to forbid automated access; the Bank has not been found to forbid
anything. Putting it there would record a determination that has not been made, which is the
mirror image of the error this ADR exists to avoid.

## Consequences

Currency normalisation works, and every guarantee it makes — a rate applied upside down
raises, a rate from after the as-of date is refused, a conversion is a recorded calculation —
holds now and does not change when a source is wired in. What is missing is only the
plumbing.

Task 25 (ALFRED, Bank of England and ONS macro series) inherits this open question and must
not proceed on the Bank as though it were settled. Task 26's cost of capital needs a risk-free
rate, which is Bank data; if this is still unresolved by then, that rate arrives as an
assumption with a justification, which is a worse but honest answer.

## What a person has to do to close this

Three reads, none of which need a browser this environment does not have:

1. **<https://www.bankofengland.co.uk/legal>** — is there a clause prohibiting automated
   access, scrapers, robots or data mining, as the FCA's terms have? If yes, the answer is the
   same as task 18's and the host goes into `REFUSED_HOSTS`.
2. **<https://www.bankofengland.co.uk/robots.txt>** — confirm the four `Disallow` lines above,
   verbatim.
3. **<https://www.bankofengland.co.uk/boeapps/database/help.asp>** — which path actually
   serves CSV, and does the Bank document it as a route for programmatic use? A documented
   download endpoint is a different thing from a scraped one.

If all three come back clean, wiring it is small: a `Provider`, an allowlisted host, a rate
limit, a parser for the CSV, and rates persisted as facts with the OGL attribution in the
licence note. The hard part is already done and tested.

The alternative, if any of the three comes back badly: the European Central Bank publishes
euro reference rates with a documented API, and rates for pairs not involving the euro can be
crossed through it — at the cost of a cross-rate being a second calculation rather than a
published observation. That is a real cost and should not be paid before it is necessary.
