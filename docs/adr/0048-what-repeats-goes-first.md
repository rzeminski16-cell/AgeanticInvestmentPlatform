# 0048 — What repeats goes first, so it can be cached

Date: 2026-08-10
Status: Accepted

## Context

Gap A14: `cache_control` appeared nowhere in the codebase. Everything downstream of it was
already built — `Usage` carries `cache_read_tokens` and `cache_write_tokens`, `_usage_from`
reads them off the SDK response, `costs.py` prices a read at a tenth of input and a write at
a quarter more, and `CostKind` has categories for both. All of it reported zero, because
nothing was ever cached.

Two facts decided the shape of the fix, and the first one contradicts how the gap was
written.

**The shared system prefix is too short to cache.** `PLATFORM_CONTRACT` is about 183 tokens.
The minimum cacheable prefix is 512 tokens on Opus 5, 1024 on Sonnet 5 and 4096 on Haiku 4.5.
A breakpoint there writes nothing at all — no error, no entry, `cache_creation_input_tokens`
of zero. The gap's premise, that ordering the contract first was most of the work and only
the marker was missing, was wrong about where the tokens are.

**The tokens are in the user turn, behind content that varies.** Role input caps run from
16k to 90k, and what fills them is the evidence listing. `SectionWriterAgent.user_message`
put the section title, the section's focus and then the evidence, in that order. Caching is a
strict prefix match, so a large block that repeats is worth nothing when a per-section
sentence sits in front of it. No arrangement of markers fixes that; the order has to change.

How much actually repeats is measurable. The nineteen built-in sections resolve to a handful
of distinct evidence policies — one group of seven, one of three, two of two — and
`gather_evidence` is a function of the policy, so sections sharing one are handed a
byte-identical listing. Every retry of a single section is handed the same listing again,
up to three attempts.

## Decision

**The repeating part of a turn is declared separately and sent first.** `Agent.stable_context`
returns the head of the user turn that repeats across calls; `Message.cache_prefix` carries
it; the provider sends it as its own content block with a breakpoint after it, and the
varying part follows as a second block. `SectionWriterAgent` puts the evidence policy and the
evidence listing there, and keeps the section, the focus and any refusals to fix in
`user_message`.

**The default is not to ask.** `stable_context` returns empty unless a role overrides it, and
a turn with no prefix is sent exactly as before — a plain string, no blocks, no premium.
Marking a block that does not in fact repeat costs a 1.25× write on every call and returns
nothing, which is worse than leaving the cache alone.

**The system prompt is marked unconditionally**, even though it is currently below every
model's minimum. It costs nothing when it is too short, and it is the block most likely to
grow.

**Five-minute TTL, not one hour.** A run makes its calls in a burst of minutes, so the short
form covers the reuse that actually happens at a 1.25× write premium rather than 2×.
Break-even is two requests instead of three.

An unintended benefit worth recording: the evidence now precedes the instruction rather than
following it. For a turn that carries quoted documents, instruction-last is the better order
anyway.

## Consequences

The archived request payload changes shape: `system` is a list of one text block rather than
a string, and a split turn's `content` is a list of two. Anything reading archived payloads
by index needs to know that. Two provider tests asserted the old shape and now assert the new
one.

**Composed prompts change, so `prompts` rows re-hash and drafts may differ.** Moving the
evidence ahead of the ask is a real change to what the model reads. Section outputs are not
expected to change in substance — the same material and the same instruction, reordered — but
they are not guaranteed identical, and a golden-output comparison across this commit would
show differences that are not faults.

**The saving is not yet measured.** No live run has been made since. The mechanism is in
place and the counters exist to prove it either way; what the hit rate actually is belongs to
A15's costs page, which reads `cache_read_tokens` against `input_tokens` per run. If the
figure comes back at zero, a silent invalidator is at work and the first place to look is
whether two calls that should share a prefix really produce identical bytes.

The failure modes here are all quiet — a marker in the wrong place, a dictionary serialised in
a different order, a per-call string that crept into the prefix — so `tests/test_prompt_caching.py`
asserts on request bytes rather than behaviour, including that two sections sharing a policy
produce byte-identical blocks and that `sort_keys=True` survives.
