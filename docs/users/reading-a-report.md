# Reading a report

How to read the output, how to check any figure yourself, and how to interpret the things
the platform declines to tell you.

---

## The document

An approved report is frozen into three notations — Markdown, HTML and PDF — each hashed
and archived. `/reports/{id}` shows the report, its hash and a link to the archived bytes;
`/reports/{id}/preview` shows it as the document rather than as a web page.

The Markdown archive is the canonical one. The PDF is made from the same HTML you can
preview, so what you read on screen and what you file away are the same artefact in
different clothes.

**Every report carries its disclaimer on its face.** It is not a footer that renders when
somebody remembers to add it.

## Walking a figure back to its source

This is the part worth learning, because it is the reason to use this rather than a
chatbot.

**Every numeric footnote marker is a link.** Click it and `/runs/{id}/footnotes/{n}` shows
what that marker rests on: the excerpt, the verifier's verdict, and the digest of the
document it came from. There are only two possible answers, and the page always gives one
of them:

- **A stored fact.** The excerpt from the filing, the archived document it appears in, and
  its hash. You can re-open those bytes and read the sentence yourself.
- **A recorded calculation.** The formula, every input with its unit and its own source,
  and the version of the code that produced it. `/calculations/{id}` walks it down to its
  leaves: filed facts and approved assumptions, each with a document behind it.

There is no third answer. A figure that is neither of those does not render.

## What each surface is for

| Surface | What it answers |
|---|---|
| `/runs/{id}/claims` | Every claim the report asserts, and whether its evidence verified |
| `/claims/{id}` | One claim, its excerpts, and the verifier's verdict on each |
| `/runs/{id}/sources` | Every document the run acquired — **including the ones it refused** |
| `/calculations/{id}` | One figure, walked to its leaves |
| `/companies/{id}` | This company's research history: valuation range over time, prior catalysts and risks |
| `/reports` | Every report this account has produced, grouped by company |

The sources page is the one people skip and shouldn't. A run that refused four documents
made four judgements, and the reasons are on that page.

## Reading a refusal

The platform withholds a lot, deliberately, and a withheld figure is not a bug report. Each
refusal states its reason on the page.

**"This figure is withheld."** The chain behind it is incomplete or the number is not
plausible. Plausibility is a small closed set of relations that cannot hold on a
consolidated statement — a net margin above 1, net income above revenue — checked in code
over the recorded calculations. When one fails, the whole block withholds itself and says
so, rather than rendering a number that is traceable *and impossible*. Traceability and
sanity are different properties, and that distinction was learned from a real run that
published a 172.1% net margin with every guard holding perfectly.

**"Not meaningful for this sector."** The ratio is not computed at all — so there is no
calculation for a citation to reach — and it comes back carrying the reason. Debt-to-equity
on a bank excludes deposits, which is almost all of a bank's leverage, so reporting it
would be worse than omitting it.

**"This filing does not report X."** Distinct from the above. The line genuinely is not
there.

**A thin evidence pack.** Evidence reaches a section ranked, and if a section had little to
work with the report says so rather than writing confidently from three facts.

**No comparables.** Either you have not confirmed a peer set, or the licensed data cannot
be republished in the form requested. Derived figures may be published by your own
determination; the underlying series may not.

## What the red team is for

The bear case at gate 3 is not a formality and it is not a second opinion. It works from
its own context, sees the draft rather than the drafting, and is scored for finding
problems. It has caught real ones — including an impossible margin that every deterministic
gate had passed.

Read it as an adversary's best attempt, and weigh it. It advises; it does not block.

## Exporting

`POST /reports/{id}/export-obsidian` projects one approved report into your vault. It is
**never automatic**: the projection is one-directional and only ever from approved data, so
nothing in the vault can flow back and become evidence.

## What a report does not establish

- **It is not advice.** The rating is a non-binding personal view.
- **A green validation suite is not a correct thesis.** The checks establish that the
  document is *supported*, not that its argument is *right*.
- **Coverage varies by filer.** The concept map does not know every filer's vocabulary. A
  run that swept many unmapped tags produced a thinner report, and the run record says how
  many.
- **Prices and betas are licensed data.** They may be absent if the subscription is not
  configured, and the report will say so rather than substituting something else.

---

**Next:** [the portfolio](portfolio.md) · [troubleshooting](troubleshooting.md)
