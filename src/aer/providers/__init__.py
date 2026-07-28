"""Model providers: one door out to a language model, as ``aer.fetch`` is to the network.

Three rules, and they are the same shape as the ones governing network egress, for the
same reason: a capability that can be reached from anywhere is a capability nobody can
audit.

**Only :mod:`aer.providers.anthropic` may import the ``anthropic`` SDK.** Enforced by a
test that scans the source tree. Not because a second provider is imminent, but because
the day one is wanted, the alternative is finding every call site — and by then the
call sites are the whole codebase.

**Every call goes through the router.** A model identifier never appears at a call site.
An agent asks for a *role* — ``planner``, ``red_team`` — and :mod:`aer.providers.router`
decides which model and how much effort. That makes changing the cost profile of a run a
configuration edit rather than a code change, and makes "which model produced this?" a
question with one answer per role rather than one per file.

**Every call writes a cost row.** Metering is not a reporting feature bolted on later; it
is how the budget cap works, and a cap that only warns is a cap that does not work.
"""

from __future__ import annotations
