# ADR 0092 — The web is searched as a listing, and read only through the gate it already has

**Status.** Accepted
**Date.** 2026-08-28
**Extends.** ADR 0036 (workers request tools and code executes them), ADR 0071 (a tool is
a registered capability), ADR 0019 (detection is not the defence), ADR 0009 (network
egress is deterministic and guarded).
**Admits.** The `web_search` tool capability, to the `analysis` role alone (per ADR 0035
this admission is recorded here). No new agent role: the capability joins the five
research workers' existing allowlist.
**Required by.** Roadmap §3.14, and it closes the roadmap's outstanding commercial
check 1.

## Context

Nothing has a `web_search` capability. `fetch_known_url` reaches only a host this run has
already acquired a document from, and refuses anything else by design. The trust model
for search-found material has existed since before this item — `sources/tiering.py` and
`fetch/policy.py` carry `Provider.WEB_SEARCH`, with search-found news and issuer material
at `T5_SECONDARY` and search-found commentary at `T6_UNVERIFIED` — and the cost table has
carried a `web_search` category since the `costs` table was designed. What was missing was
a tool nothing calls, and a price nobody had verified against a primary source.

## The commercial check, closed

Verified 2026-08-28 against the official pricing page
(`https://platform.claude.com/docs/en/about-claude/pricing`, "Web search tool"):

> Web search is available on the Claude API for **$10 per 1,000 searches**, plus standard
> token costs for search-generated content. [...] Each web search counts as one use,
> regardless of the number of results returned. If an error occurs during web search, the
> web search will not be billed.

That is $0.01 per search, which is what `aer.providers.costs.WEB_SEARCH_USD_PER_CALL`
records, beside the token pricing the meter already applies. With the price verified, the
fee enters the budget guard the only way invariant 6 admits: every search writes a
`costs` row (category `web_search`, the category that was waiting for it), the per-worker
step estimate covers the bounded searches, and the guard reads the same table it always
has.

## Decision

### The model asks; code executes; the answer is a listing

`web_search` is wired exactly as every other worker tool (ADR 0036): the worker puts a
`ToolRequest` in its turn, and deterministic code decides and executes. Execution is one
dedicated, metered call through `aer.providers` — the only module that may speak to the
vendor — running the vendor's server-side search tool on the cheap routed model
(`web_search` in the routing table), bounded to one search per request.

**What comes back is a listing, never a page**: title, URL and the index's age note per
result — the same shape `search_filings_full_text` established, and for the same reason.
A search result's text is not an artefact this platform holds; a snippet quoted into a
prompt would be externally derived text with no hash behind it. Titles and URLs are
external text, so they reach the worker only inside the untrusted wrapper (ADR 0019),
labelled `T6_UNVERIFIED`: colour and leads, never citable — the validator already refuses
any citation of something that is not a held id, so the containment is structural rather
than asked for.

### Reading stays behind the gate that already exists

This item widens what a worker can *find*, and deliberately not what the platform will
*read*. `fetch_known_url` keeps its rule unchanged: a host is never taken from a request,
and a search-found URL on an unadmitted host is refused exactly as before. The
`T5_SECONDARY` tiering rows for `Provider.WEB_SEARCH` describe the day a search-found
page is fetched, hashed and recorded — and that day arrives per publisher, through an
adapter-style ToS/robots determination recorded in an ADR before the first request, never
as a batch of sources assumed trustworthy for being well known. The roadmap's own check
stands as the warning: Seeking Alpha's robots.txt disallows `Claude-User` and `ClaudeBot`
by name, and `ft.com` could not be checked at all. No publisher is pre-approved here.

### Point-in-time refuses what it cannot bound

A live index cannot be filtered by an as-of date, and a result's own date line is
external text an attacker or a sloppy publisher controls. So the executor refuses
`web_search`, deterministically and with the reason stated, whenever the run is
point-in-time and its as-of date is before today. A run researching the present — the
ordinary live case, where nothing published after today can exist — searches freely.
Invariant 4 stays enforced at acquisition, in code.

### Bounds, and who holds the capability

- **Three searches per worker node**, counted in code; the fourth is a refusal naming the
  bound. Five workers means at most fifteen searches — at most $0.15 — per run, plus the
  small routed calls that carry them, and the worker step estimate rises to cover it.
- **The `analysis` role alone.** The writers hold no tools (ADR 0042), the red team must
  argue only from what the draft saw (ADR 0039), the planner reads the request and
  nothing else, and `calc/` consumes structured facts extracted from filings — so a T5 or
  T6 source has no route into a number whatever tool exists. That boundary is the one
  rule, not a permission this ADR could move.

## Consequences

- The qualitative workers — recent developments above all — can see this week's headlines
  instead of finishing with five leads and no findings for want of anything recent in
  front of them.
- Every search is a `costs` row the caps can see, at a verified price; a failed search
  costs nothing, which matches the vendor's own billing.
- No new ingestion surface exists: nothing fetched, nothing hashed, nothing citable came
  from this item. The first publisher anyone wants always-on reading from gets its own
  ADR, its own robots/ToS determination, and its own adapter.
- A test run with no search-capable provider simply never binds the executor, and the
  worker's menu says so — the same degradation `fetch_known_url` has always had.
