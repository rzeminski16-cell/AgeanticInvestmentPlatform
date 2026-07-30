# 19. Detection is not the defence

- **Status:** Accepted
- **Date:** 2026-07-30

## Context

Threat T2 is prompt injection from filings and web pages. The obvious response is a scanner: look
for *"ignore your instructions"*, flag the document, refuse it.

That instinct produces a system that is worse than useless, for a reason worth stating plainly.
A scanner made of regular expressions can be got around by anyone who reads the regular
expressions — and this repository is public. If refusal depended on detection, the platform's
resistance to injection would be exactly as good as a phrase list, and every payload phrased
differently would sail through with a clean bill of health attached.

So the question is not "how good can the scanner be?" It is **"what would still hold if the
scanner noticed nothing at all?"**

## Decision

### Containment is structural, and it comes first

Three properties, none of which involve reading a document:

1. **No agent has a network tool.** There is no agent-callable function anywhere in the
   platform that takes a URL. An injected *"send the database to evil.invalid"* has nothing to
   call, so exfiltration is not mitigated — it is unavailable. Only `aer.fetch` reaches the
   network, and it is driven by deterministic code with a domain allowlist.
2. **`allowed_tools` is a class attribute, checked in Python.** Nothing reads a payload, a
   document, or a model response to decide it. A test parses `aer/agents/base.py` and asserts
   the attribute is never assigned at runtime — so there is no path from what a document says to
   what an agent may do, and no amount of persuasion creates one.
3. **Untrusted content is delimited and labelled** by the agent base, not by the agent.

`tests/test_injection.py` puts those first, and its first class is called
`TestContainmentDoesNotDependOnDetection` because **those tests would pass with the scanner
deleted**. That is the claim being made, and it is checkable.

### The wrapper is a mitigation, and says so

`<untrusted_source id=… tier=…>` makes the boundary legible to the model, so a document's
instructions read as a quotation rather than as a turn in the conversation. Worth doing; it
costs nothing. It is not a control, because it works by the model reading something correctly —
and a defence that depends on a language model noticing a trick is exactly what this
architecture exists to avoid relying on.

**One thing in it must be right: the delimiter cannot be escaped from inside.** A document
containing `</untrusted_source>` could otherwise close its own quotation and continue as though
its next paragraph were the system's own frame — the attack executed against the mitigation
meant to describe it. Both forms are rewritten, opening and closing, in any casing and spacing,
because a document that opens a nested block escapes by closing twice.

The brackets are **escaped rather than the text deleted**: a reviewer reading the archived prompt
should see exactly what the document attempted. A silent deletion leaves the passage reading
innocently, and a homoglyph substitution just replaces one confusable string with another.

An earlier version of the test for this was **vacuous in three of its five cases** — it matched a
lowercase literal, so `</UNTRUSTED_SOURCE>` and `</untrusted_source >` passed without the
neutralisation doing anything. Caught by removing the neutralisation and noticing only two cases
failed. The assertion is now an independently written case-insensitive pattern.

### Sources are declared, not interpolated

An agent returns `untrusted_sources(payload)` and the base wraps them. An agent that pasted a
filing into its own `user_message` could forget to delimit it, and the one that forgets is the
one that gets exploited. The default is empty, so every agent gets containment for free and opts
*in* to carrying documents rather than opting out of protection.

The containment rule is appended to the system prompt only when a call actually carries content.
Adding it unconditionally would put a rule about quoted documents in front of an agent that reads
none, and the prompt recorded against a run should describe that run. The two variants hash
differently and become two `prompts` rows, which is correct — they are different instructions.

Token counting uses the composed forms, because those are what get sent. Counting the bare prompt
would under-report by the length of every quoted document, which is most of the call — and that
figure is what a person sees before agreeing to spend money.

### A finding is a flag, never a block

Nothing refuses a document. Every signal has an innocent explanation: a print stylesheet, an
accessibility label, a base64 image, a script using zero-width joiners, a filing that genuinely
discusses prompt injection. A scanner that quarantined every filing using `display:none` would
quarantine most of them.

`injection_flagged` is therefore kept **separate from `quarantined`**. Quarantine is the
point-in-time rule and is a refusal; a flag is information for a human at gate 2. Collapsing them
would make "this is not admissible evidence" and "this is worth a second look" indistinguishable,
which is the distinction a reviewer needs most.

### Scanning happens inside the sandbox

"This text was invisible" is a fact about the markup, and the markup exists only during the parse
— which already runs in a child process, because untrusted bytes are driving a C library. Scanning
there means one parse. Re-parsing untrusted bytes *outside* that boundary in order to look for
attacks would be an odd way to defend against them.

The pattern half (`scan_text`) needs only a string, so it is pure, safe anywhere, and the PDF
extractor inherits it without a line of new code.

### Findings carry locators

"This document contains hidden text" is not checkable. "Characters 4,102 to 4,190 of the extracted
text were inside a `display:none` element, and they say this" is — and a reviewer can be shown the
passage. Same contract as citations, for the same reason.

## The corpus, and the false positive kept on purpose

`tests/injection_fixtures.py` holds 26 poisoned documents: hidden by six different mechanisms,
white-on-white, off-canvas, comment-borne, plain-text overrides, chat-format role markers,
exfiltration lures, zero-width smuggling, encoded blobs. All 26 are detected, and all 26 are
contained regardless.

**The clean documents are the more valuable half.** A scanner that flagged everything would score
perfectly and be worthless, because a badge on every filing is a badge nobody reads. `FILINGS`
contains prose a careless heuristic trips over: an accounting policy that uses "disregard", a long
accession number, an ordinary build comment, a risk factor about competitors ignoring pricing
discipline. None is flagged.

`INNOCENT_BUT_FLAGGED` is the third category and the one worth arguing about. A print-only
appendix in a `display:none` block **is** hidden text. The scanner cannot see intent and says so.
Suppressing it would mean requiring an instruction-shaped phrase before reporting hidden content,
which would miss the next payload phrased differently — and heuristics are already bad enough at
novel phrasing. **The false positive is kept, listed, and paid for in badges rather than in blind
spots.**

## Consequences

**Good.**

- The claim "0 tool-policy violations across the injection corpus" is true by construction rather
  than by measurement, and the tests that establish it do not mention the scanner.
- A reviewer at gate 2 is shown passages, not categories.
- A future agent that wants a tool has to add it to a class attribute, and a test names the
  network-shaped names it may not use.

**Costs, accepted.**

- The scanner will miss novel phrasings. That costs a badge, not an exploit.
- The known false positive above.
- Two prompt rows per agent that reads documents, one with the containment rule and one without.

**Deliberately not built.**

- **Automatic quarantine on a finding.** The reasoning above; revisit only with evidence that
  reviewers are ignoring badges.
- **A model-based injection classifier.** It would be better at novel phrasing and it would put a
  language model in the detection path, where its failures are unpredictable and its cost is per
  document. The structural controls do not need it, so it can wait for Phase 4's validator, where
  a judge already exists.
