# ADR 0033 — A credential in a URL is invisible to name-based redaction

**Status.** Accepted
**Date.** 2026-08-05
**Amends.** ADR 0009, which made the fetch layer the single guarded door out to the network.
**Found while.** Building the EODHD adapter (task 29), which is the second provider to take
its API key as a query parameter.

## Context

`aer/logging.py` redacts two ways, and the module docstring is explicit about both:

- **By field name.** A log field called `api_key`, `authorization`, `password` and so on is
  masked whatever it contains.
- **By value shape.** A string matching `sk-ant-…`, `Bearer …`, `ghp_…` and similar is masked
  wherever it appears, including inside a longer sentence.

Between them those cover a credential passed as a header or logged as its own field. They do
not cover a credential **inside a URL**, and two providers put one there.

FRED takes `api_key` in the query string. EODHD takes `api_token`. Task 25 saw this and gave
`aer/sources/macro/client.py` its own `redacted()` helper, used on the macro client's log line
and on the recorded source-document URL. That was correct as far as it went, and it did not
go far enough.

**Two leak paths remained open, and both were live.**

1. **`SafeFetcher` logs the URL itself.** `fetch.completed` fires on every response and
   `fetch.retrying` on every retry, each carrying `url` and `final_url`. Those are the
   fetcher's own log calls; no adapter-level redaction can reach them. The field is named
   `url`, which is not a sensitive name, and the value is a whole URL, which matches no
   credential shape. The FRED key went out in full on every fetch.

2. **`httpx` logs the request line.** `HTTP Request: GET <url> "HTTP/1.1 200 OK"`, at INFO,
   from a library this codebase does not control. `configure_logging` does bridge foreign
   stdlib records through the same redaction — that part worked — but the message is a plain
   string, and the same two blind spots apply to it.

Beyond logs, `FetchResult.url` is what a service writes into `source_documents.url`. That
column is permanent, appears in a report's sources appendix, and outlives the subscription
that issued the key.

**The existing test for this passed, and could not have failed.**
`tests/test_smoke.py::test_foreign_stdlib_records_are_redacted` logs an httpx-style line
containing `?token=sk-ant-api03-FAKEFAKEFAKE` and asserts the key is absent. It passes because
`sk-ant-…` matches a value-shape pattern anywhere in any string — the fact that it was inside
a URL was incidental. A FRED key is a bare 32-character hex string that matches nothing. The
test asserted the right property with a value that could not exercise it.

## Decision

### The parameter list lives in `aer/logging.py`, which is the redaction authority

`CREDENTIAL_PARAMS` names the query parameters that carry a credential — `api_key`,
`api_token`, `token`, `access_token`, `secret` and the rest — and a pattern built from it
joins `_SECRET_VALUE_PATTERNS`. Every log line, from any logger, in any library, now has the
value after one of those parameters replaced.

Putting it here rather than in the fetch layer is the point. The leak that mattered most came
from `httpx`, and nothing this codebase writes can intercept a third-party library's log call
except the redaction processor every record already passes through.

**Bare `key` is deliberately excluded.** It is a legitimate non-secret parameter in several
APIs — FRED's own `series_id` sits beside it — and redacting it would hide something a reader
needs while protecting nothing.

### The fetch layer redacts every URL it records, for every provider

`aer/fetch/credentials.py` reuses the same list and rewrites URLs. `SafeFetcher` applies it
to `url`, `final_url` and the redirect chain on the `FetchResult` itself, to every log line,
and to every error context and message that carries a URL. `aer/fetch/ssrf.py` and
`aer/fetch/robots.py` do the same for theirs.

So an adapter cannot leak a query-string credential by forgetting to call anything. The
guarantee is a property of the layer that does the recording, which is where guarantees
belong — the same argument ADR 0009 made for putting SSRF, robots and rate limiting behind
one door rather than in each adapter.

`aer/sources/macro/client.py`'s `redacted()` keeps its name, because callers and tests use it,
and now delegates.

### The parameter name survives; only the value goes

`api_token=REDACTED`, not `REDACTED`. A reader can still tell an authenticated request from an
anonymous one, and the rest of the URL is what makes a fetch reproducible.

### Matched on the parameter, never on the key's value

A key that has been rotated is still hidden in an old log line, and a key that looks like
ordinary text is still hidden in a new one. Matching values would fail at both ends, which is
exactly how the original test came to pass for the wrong reason.

## Consequences

**One list, two enforcement points.** A parameter name added for a new provider closes the
hole in the logs and in the recorded URLs at once. Two lists would have drifted.

**A URL that is not a credential pays one failed regex.** Accepted.

**Historic log files and `source_documents` rows written before this change may contain the
FRED key.** Nothing here retroactively cleans them, and the honest mitigation is to treat that
key as exposed and rotate it. This ADR is where that is written down rather than left implied.

**`caplog.text` is not evidence either way.** pytest attaches its own handler with its own
formatter, bypassing the redaction processor entirely, so a test asserting on `caplog.text`
would be asserting about pytest. The tests capture configured output instead.

## Alternatives rejected

**Redact only in the fetch layer.** Closes the leak this codebase writes and leaves the one
`httpx` writes wide open. It was the first fix attempted and the httpx line is what showed it
was not enough.

**Put credential parameters on `FetchPolicy`, per provider.** Correct until somebody adds an
adapter and forgets, and the cost of forgetting is a credential in a permanent database row.
Redacting anything that looks like a credential parameter is safe by default.

**Move the key to an `Authorization` header.** Not available: the provider decides how it
accepts authentication, and both FRED and EODHD accept it only in the query string.

**Rely on `is_sensitive_name` catching `api_key` as a field name.** It already does, and it
never sees inside a URL. That is the whole finding.
