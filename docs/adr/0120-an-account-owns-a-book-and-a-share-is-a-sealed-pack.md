# ADR 0120 — An account owns a book, and a share is a sealed pack

**Status.** Proposed — V1.0_Alpha, **deferred**. Drafted now so that V1.0's schema does not
foreclose it; not implemented in V1.0. Accepted when the gating condition below is met and the
change lands.
**Extends.** ADR 0073 (an attestation is what the book says, at two times and one grade of
evidence), whose rule that an attested lineage reaches no shareable surface becomes
load-bearing the moment a shareable surface exists. ADR 0116 (a report is superseded) names the
index this ADR widens.
**Required by.** `docs/V1.0_Alpha/04-feature-specifications.md` F17, and open question 5,
answered by the operator on 14 September 2026.

## The gating condition, stated first

**This is not built in V1.0.** The operator's decision on 14 September was: single user for
V1.0; build every other feature, including the closing section that reads the book; defer this
until **a solicitor has read the consequences-not-instructions design** — the distinction
between computing what a position does to a book and telling somebody what to do.

This ADR is drafted anyway, for one reason: **V1.0's schema and its service layer are being
written now, and the cheapest moment to not foreclose accounts is before the tables exist.**
Everything below that says "in V1.0" is a constraint on work happening this quarter. Everything
else is the shape the decision takes when it is taken.

## Context

`get_current_user` returns the first row of `users`, and its docstring says exactly why:

> The MVP has no authentication and exactly one user. This dependency exists anyway so that
> every handler is already written against "the current user" rather than against an implicit
> singleton — when authentication arrives it replaces the body of this function and nothing
> else.

That was good discipline and it is true of the handlers. It is **not** true of the queries.
`visible_facts`, the report library, the position store, the thesis reader and the decision
reader all scope by company or by request, never by user, because there has only ever been one.
Replacing the body of `get_current_user` would give every account the same book.

There is no inbound rate limiting and no deployment story. The roadmap records both as
*"before this leaves one machine"* and treats them as hygiene.

**If the product strategy is right, they are not hygiene.** The buyer most likely to pay for an
auditable research record is one who has to defend their research to somebody else — a
committee, a client, a co-investor, a regulator. For that buyer the ability to hand over the
work *and have it survive scrutiny without them present* is not a feature around the product;
it is the product. Everything else this platform does — the hashes, the citation verification,
the calculation lineage — exists to make a document defensible, and today the document cannot
leave the machine that made it.

## Decision

### 1. An account owns a book, and the scoping is in the query layer

Every row that represents the operator's own position or judgement — requests, reports, theses,
premises, decisions, positions, fills, attestations, watchlist entries, findings, skills —
carries a `user_id`, and **the scope is applied where the query is built, not where the route
is handled**. A service function that can be called without a user scope is a service function
that will be, and route-level checks are how every multi-tenant data leak in the world has
happened.

Shared reference data — companies, filings, financial facts, source documents, artefacts — is
**not** scoped. It is the world, not the book. Two accounts researching Microsoft read the same
hashed 10-K, which is the point of a content-addressed store.

ADR 0116's partial unique index becomes `(user_id, company_id)`. That is the one line this
deferral is really protecting: get the column in at the right time and it is a migration; get
it wrong and it is a rewrite.

### 2. A share is a sealed evidence pack, not an account

Sharing does not mean giving somebody a login. It produces a **read-only evidence pack**: one
artefact containing the report, every source document it cites, every calculation with its
formula and inputs, the verification record, and the provenance of each — openable by somebody
with no account, and checkable without the platform.

- **It is deterministic and hashed.** The same report produces a byte-identical pack, and the
  pack is itself an artefact in the store with its own hash. A pack somebody was sent can be
  proved to be the pack that was made.
- **Every footnote resolves inside it.** That is the acceptance test and it is the whole
  proposition: a reader who does not trust the sender can still walk a figure to bytes.
- **It is a snapshot, and says so.** The pack states the report it came from, when it was
  sealed, and that a newer report may supersede it (ADR 0116). It does not phone home, and it
  does not update.

### 3. No attested figure leaves in a pack

ADR 0073 established that a lineage containing an *attested* node — what the operator's own
book says: a holding, a fill, a cash balance — reaches no shareable surface, structurally,
because the type it propagates into has no field for the figure. **The evidence pack is a
shareable surface**, and this is where that rule earns its keep: the closing section's
consequences for the operator's book, the position weights, the concentration figures and every
portfolio-derived number are absent from a pack, by type rather than by filter.

A pack is the research. The book stays home.

### 4. Authentication is the boring kind, and stays small

Sessions with server-side state and hashed passwords; inbound rate limiting on the auth
endpoints and the request form; no third-party identity provider; no organisations, no roles,
no invitations. Two accounts with separate books is the target, not a tenancy model.

The deployment story stays *one machine, the operator's own*. Accounts make the platform
usable by a second person; they do not make it a hosted service, and nothing here should be
read as deciding that it becomes one.

### 5. What sharing does not make the platform

A pack carries the operator's stated view (ADR 0117) **attributed to the operator**, with its
basis and its falsifier, and the disclaimer every user-facing surface already carries. It
carries no rating the platform produced, no recommendation, and no figure about the recipient's
circumstances — it cannot, because it knows nothing about them and because rule 3 removes the
only personal figures in the system.

Personalised facts, generic conclusions. The personal half never leaves.

## What is given up, named rather than discovered

**Collaboration.** Two people cannot work on one book, comment on a report, or hold different
theses about the same company with a shared evidence base. The pack is one-way and read-only.
That is a product this ADR deliberately does not build.

**Revocation.** A pack that has been sent cannot be recalled, because it is a file rather than
a link. That is a deliberate trade for "checkable without the platform" — and the alternative,
a hosted link that can be revoked, requires the hosted service this ADR declines to become.

**Freshness.** A pack goes stale the moment the report is superseded, and the only thing
mitigating it is the sentence in the pack saying so.

## Consequences

**For V1.0, which is not building this**, three constraints on work happening now:

1. **Write `user_id` into the tables when they are created**, even while exactly one user
   exists and every query ignores it. A column added later is a migration over live data; a
   column present from the start is free.
2. **Never add a service function whose only scope is a company or a request** where a user
   scope will be needed. The scope parameter can be defaulted to the single user; the parameter
   itself is what is expensive to retrofit.
3. **Keep ADR 0073's type discipline intact.** The attested-figure rule is currently protecting
   a surface that does not exist. Weakening it because nothing shares yet is how it would be
   gone when something does.

**When it is built**, the acceptance test is two sentences: two accounts exist with separate
books and neither can read the other's; and a pack opens for somebody with no account and
resolves every footnote.

## Alternatives considered

**Hosted multi-tenancy with organisations and roles.** The shape a SaaS product takes, and a
different product from the one the roadmap describes. It brings hosting, a security surface,
a data-protection posture and an operational burden, in exchange for collaboration nobody has
asked for.

**A read-only share link served by the operator's own machine.** Cheaper than a pack and
revocable. It requires the operator's machine to be reachable by the recipient, which is the
deployment problem this ADR avoids, and it makes the evidence dependent on the platform being
up — which is precisely what a defensible record must not be.

**Export the existing PDF and call that the share.** It is what exists today. The footnotes do
not resolve, the sources are references rather than documents, and the calculation lineage is
absent — so the recipient has a document that asserts it is checkable and is not.

**Build it in V1.0 anyway.** The operator's own reason for deferring is the right one: the
consequences-not-instructions boundary is a legal question, not an engineering one, and
building the surface that publishes across it before somebody qualified has read the design is
the wrong order. The schema constraints above cost almost nothing and keep the option open.
