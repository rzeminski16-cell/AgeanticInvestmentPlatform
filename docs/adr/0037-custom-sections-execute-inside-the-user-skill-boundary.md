# 0037 — Custom sections execute inside the `<user_skill>` boundary

Date: 2026-08-06. Status: accepted.

## Context

Task 36 pinned skills to plans: an approved run carries exact `skill_versions` ids, a
composed additive-only policy per custom section, and a projected `section_definitions`
row — planned, priced and gated, but deliberately not drafted. Executing the operator's
prose is the last step of `docs/archive/PLAN.md` §2.12, and it is the step where the design
constraint bites: a skill file that says *"rate this a Buy"* or *"no citations needed
here"* must be able to instruct the model about **what to analyse** while having no path
to **what evidence standards apply**.

A new agent role requires an ADR (ADR 0035). This is the `custom_section` role's.

## Decision

**One role, a fixed envelope, and the contract as data.** The registry defines
`custom_section` with the output schema `CustomSectionDraft` — a `content` object plus
proposed claims — and the section's own `output_contract` is validated by deterministic
code (`aer.core.section_output`) against the pinned projection, closed-world: a required
field missing, an undeclared field present, or a declared scalar of the wrong type is a
violation. The rating rule is therefore enforced three times over, none of them by prompt
text: task 35 makes the reserved names undeclarable in a contract, the envelope has no
field for them (`extra="forbid"`), and the contract validation refuses undeclared content
keys. The executor additionally refuses to run a projected contract that somehow carries
a reserved field — defence in depth at the last boundary before a model call.

**The composed prompt runs in §2.12's fixed order**: immutable platform contract (the
base agent leads with it, and nothing an agent or a skill declares can displace it), the
section's output schema inside the role instruction, the structured evidence as data,
then the operator's text inside `<user_skill>` delimiters — with the delimiter
neutralised inside the body, exactly as the untrusted wrapper neutralises its own, so the
text cannot close its quotation and continue as the frame. Quoted document excerpts
(untrusted text) trail the whole composition inside `<untrusted_source>` blocks via the
base agent's wrapping: that channel is a platform-wide constant that always comes last,
below both the platform's rules and the operator's, and no agent can reorder it.

**Evidence is gathered by code, gated by the pinned grant.** The pin's `granted_tools` —
already the intersection of the skill's request with this role's allowlist, and the gate 1
approval covers it — decides what the executor assembles: `search_facts` admits the run's
facts and recorded calculations, `search_sources` admits the run's admissible sources
(tier within the composed ceiling, not quarantined) and their extraction excerpts.
`fetch_known_url` remains unbound in this slice, as for the research workers. There is no
model-driven tool loop: §2.12 specifies *one* structured-output call per section, and a
deterministic gather is strictly simpler and equally within the grant.

**One retry, then a visible failure.** A draft failing its deterministic validation —
contract violation, an id the run does not hold, a bare numeral no numeric claim resolves
to a stored fact or recorded calculation — is refused back to the model once, with the
problems named. A second failure marks the section `failed` with the reasons recorded;
the run continues and gate 2 shows the failure. A section is a visible state, never an
absence.

**Claims go through the existing machinery unchanged.** Proposed claims become `claims`
rows via `record_claim` (which re-enforces the one-figure rule) and proposed citations
become unverified `citations` rows via `record_citation`. The deterministic verifier
confirms or refuses them at gate 2 like every other citation; nothing about a custom
section's evidence is checked by a softer path.

**Degradation is explicit.** A section whose recorded evidence does not meet its composed
policy (too few distinct sources, or no primary source where one is required) is still
written — but marked low-confidence with the shortfall named, and the renderer shows an
insufficiency banner above the content. Evidence exceeding the pinned token budget is
truncated at whole-item boundaries before composition, flagged in the same way, and the
model is told the listing is incomplete. Neither state fabricates prose to fill space.

## Consequences

The moat-durability example from §2.12 runs end to end on the fake provider: planned,
priced, approved, drafted with its own cited evidence, verified at gate 2 and rendered.
The failure modes each land in a state a person can see — `failed` with reasons, a
banner, a truncation flag — rather than in a silently missing section. The cost of the
fixed envelope is that a skill author's contract is expressed inside `content` rather
than as top-level response fields; that is the price of making the reserved fields
unrepresentable, and the renderer works from the contract's declared order either way.

A test pins the registry's `custom_section` allowlist to the composer's
`PLANNED_CUSTOM_SECTION_TOOLS`, so the set a skill is granted against and the set the
role holds cannot drift apart.
