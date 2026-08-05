# 25. The gate found the verifier wrong on its first run

- **Status:** Accepted
- **Date:** 2026-08-05
- **Amends:** ADR 0018 (only code confirms a citation) — the rule stands, the comparison changes

## Context

Every guarantee this platform makes was proved once, by a test written the day the feature
landed. §2.10 of `docs/PLAN.md` names six that must keep holding, and task 21 turns them into
a blocking gate: six numbers, six thresholds, red build if any moves.

Building it required corpora with the *wrong* answers in them as well as the right ones. A
verifier scored against only-genuine citations can return `True` unconditionally and score
100%; a point-in-time filter scored against only post-dated documents can refuse everything
and score 100%. So `tests/citation_corpus.py` carries forty labelled pairs, eleven of which
are fabricated in different ways.

**The first run failed.** Two of the eleven fabrications were accepted:

| Case | Similarity | Verdict under the old rule |
|---|---|---|
| `$198,270` cited as `$198,720` | 0.971 | accepted |
| "Dividends declared were $18,135 million" cited as "…were **not** $18,135 million" | 0.951 | accepted |

Citation accuracy 0.95 against a threshold of 0.98. Hallucinated-citation rate 0.18 against a
threshold of zero.

The module's own docstring had asserted the opposite: *"The threshold is high enough that a
sentence with a different number in it, or a different subject, falls far below it. A
fabricated excerpt does not score 0.9 by accident."* That was wrong, and had been wrong since
task 12. Two transposed digits in a revenue figure and one inserted negation are the two most
damaging things a citation can get wrong, and they are precisely the two a character-level
similarity score is worst at seeing — because both are tiny edits to a long string.

## Decision

### Verification is equality after normalisation, not a similarity threshold

`aer.core.schemas.extraction.comparable` folds away exactly the differences no reader can
see: Unicode compatibility composition, invisible characters (soft hyphen, zero-width space,
byte-order mark), typographic variants (curly quotes, en and em dashes, non-breaking space,
prime), and whitespace. What survives has to match **exactly**.

Nothing else is folded — not case, not punctuation that is not a variant, not word order.
Folding case would accept "NOT" for "not". Folding punctuation would accept `$1234` for
`$1,234`, and deciding whether those are the same number is not a verifier's job.

### The similarity ratio survives as a diagnostic

Still computed on every failure, still stored on the row, still shown on the claim page. It
never admits a citation. What it does is tell an operator which kind of problem they have:
0.97 is a transposition or a stray word and worth reading; 0.02 is a fabrication and worth
refusing outright. The error message now says "nearly matches but is not the same" above
`MATCH_THRESHOLD` and "does not match" below it — the constant kept its value and lost its
authority.

### What the fuzzy tolerance was actually protecting

The original reasoning was that an extractor upgrade could reflow a document and invalidate
every citation recorded against it. That risk is real and is already handled somewhere else:
`_reread` re-extracts the artefact and compares `content_hash`, so an extractor whose output
changed is reported as *"the extractor changed, re-extract the document"* rather than as a
bad quote. The fuzzy ratio was insuring against a failure another check already owned, and
charging for it in false negatives at the far end of the scale.

The tolerance that was genuinely needed — invisible characters and whitespace — is now
explicit, enumerated, and commented one line per character.

## Consequences

**The gate passes at 40/40 and a hallucination rate of zero**, and the two cases that forced
this are pinned as their own tests in `tests/test_citations.py` so they cannot return quietly.

**Citations verified under the old rule were verified under a weaker one.**
`VERIFICATION_METHOD` is still `excerpt_match_v1`, which is now slightly untrue for any row
written before this change. Nothing has been published from this repository, so there are no
such rows to re-check; the constant is versioned precisely so that the next change to the
comparison bumps it and "which citations need re-checking?" stays answerable.

**Some correct citations will now be refused that previously passed** — an excerpt with a
real character-level difference this list does not cover. That is the right direction to be
wrong in: a false negative blocks a gate and an operator looks at it, and the override path
exists and records a reason. A false positive puts an unsupported sentence in a report and
marks it checked.

**The evaluation suite earned its place before it was finished.** That is the argument for
this whole task, and it is worth saying plainly: a corpus with deliberate wrong answers in it
found a defect in the platform's strongest control on the first run, in code that already had
a passing test suite and a written rationale for why it was correct.

## Alternatives considered

**Add a numeric-token equality guard on top of the fuzzy match.** Catches the transposed
digit and nothing else — the inserted negation has identical numbers on both sides. A guard
that fixes the case in front of you and leaves the class of failure open is worse than one
that closes the class, because it reads as though the problem was solved.

**Raise the threshold to 0.99.** Moves the line without changing what the line measures. The
negation case is 0.951 on a 56-character sentence; the same edit to a 300-character paragraph
scores about 0.993. A ratio that depends on the length of the surrounding text is not a rule.

**Lower the corpus difficulty so the gate passes.** Named because it was the fastest option
and it is the one this project exists not to take. A gate calibrated to what the platform
already does is a gate that measures nothing.
