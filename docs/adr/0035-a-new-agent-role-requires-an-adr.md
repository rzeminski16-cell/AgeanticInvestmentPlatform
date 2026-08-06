# 0035 — A new agent role requires an ADR, and the registry enforces it

Date: 2026-08-06. Status: accepted.

## Context

Phase 4 adds a dozen agent roles to a platform that has run three phases on one. The risk
`docs/PLAN.md` names for this phase is **agent sprawl** — a mesh of roles accreting
capabilities one small diff at a time — and its stated mitigation is that a new agent role
requires an ADR. A mitigation that lives in a planning document is a convention; the second
person (or the second month) forgets it.

There is a sharper version of the same risk underneath. Until now an agent's tool allowlist
and token caps were class attributes: correct today, because every agent declared
`frozenset()` and a test swept the subclasses — but structurally, capability was declared in
the same file a capability-hungry change would edit, and widening it was one attribute in
one diff. The controls this platform actually trusts do not work that way. Invariant 8's
tool authorisation is enforced in code; ADR 0034 withholds figures by removing the field
that could carry them. Capability should have the same shape: one source, refusing rather
than trusting.

## Decision

**Capability is registry data.** `aer/agents/registry.py` holds one `RoleDefinition` per
implemented role — tool allowlist, input and output token caps, output contract (by
`module:Attribute` reference, the `function_ref` idiom), purpose, and the ADR that admitted
the role. The base agent resolves its definition at construction; an unregistered role
cannot construct; a class attribute grants nothing. The input cap is enforced against a
real token count before a call is made, and the output cap is the `max_tokens` the provider
receives.

**The ADR rule is a field, not a memory.** Every definition names its ADR; the registry
refuses a definition without one; a test walks the references to the files in `docs/adr/`.
Adding a role without a decision record is therefore not a smaller diff than doing it
properly — it is a red build.

**The platform contract is the registry's too.** The immutable text every composed system
prompt begins with lives beside the definitions, leads every prompt (prompt caching keys on
a stable prefix), and is followed by the role's instruction and only then, when the call
carries fetched content, the containment rule.

Roles that are *routed* for cost configuration but have no agent yet remain the router's
concern; the registry deliberately lists only implemented roles, because a definition for
an unbuilt agent would have to invent an output contract the code does not have.

Test-only probe roles (`tests/agent_probes.py`) register under this ADR: the decision that
created the rule covers the stand-ins that prove it.

## Consequences

Adding an agent role now means: write the ADR, add the `RoleDefinition` naming it, and
build the agent against the registered contract. The registry raises on a missing ADR
reference, a duplicate role, or an agent class whose declared schema is not the registered
one. Widening a role's tools is a change to one reviewed table in a file whose whole
purpose is to be looked at — and the injection suite asserts, over the registry rather than
over classes, that no role has a network-shaped tool and that every allowlist so far is
empty. The first role to want a tool changes that test knowingly, which is the point.
