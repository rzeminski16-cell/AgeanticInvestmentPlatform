# 0050 — Settings are editable from the interface; credentials are not

Date: 2026-08-10
Status: Accepted

## Context

Gap B6/B11 asks for a settings screen — "change models, budgets and methodology without
touching code" — and Phase 6's deliverable list includes "provider-key management in
settings". The first is straightforwardly good: routing is the largest lever on what a run
costs, and making it an `.env` edit plus a restart is a poor interface for the decision this
platform asks its operator to make most often.

The second needs a decision rather than an implementation, because of something that changed
earlier the same day. `aer backup` (A10) now takes a `pg_dump` of the whole database and
writes it to a directory the operator is expected to copy somewhere safe. A credential stored
in a settings table is a credential in every one of those dumps, in plaintext, in however
many places those backups end up. `.env` is one file, git-ignored, with one copy, excluded
from the backup by construction.

## Decision

**Cost and method are editable; credentials are not.** `settings_overrides` holds model
routing, the per-run budget, the monthly budget and the warning ratio. The settings page
shows each credential as *present* or *absent* — never its value — and says where it lives.

**The allowlist is a closed vocabulary checked at the write**, not merely a set of fields the
template happens to render. A form posting `anthropic_api_key` is refused by
`save_override`, so the guard does not depend on the interface staying honest.

**The guard is asserted structurally.** `tests/test_configuration.py` walks `Settings` for
every `SecretStr` field and asserts none appears in the allowlist. A hard-coded list of
today's four credentials would pass forever while a key added next year quietly became
editable; this keeps holding as the model grows.

**Configuration is read once per run, at the start.** A run whose routing changed halfway
through would have a provenance record describing two different platforms. So an override
applies to runs that *begin* after it, which is also what an operator means by "change the
model".

**A stored value that no longer validates is ignored with a warning, not raised.** A value
that was valid under one release can stop being valid under the next, and a platform that
refuses to start because of a row in a settings table is a worse failure than one that runs
on its defaults and says so in the log.

## Consequences

The plan's "provider-key management in settings" is **not delivered**, deliberately, and the
gap analysis says so rather than showing B6 as wholly closed. If it is ever wanted, the
honest form is an OS keyring — the plan already names one for Phase 6's threat T1 — not a
database column.

One caveat on the split: budgets are now enforced from a value an operator can change from a
web page with no authentication in front of it (A5 is open, deliberately, for a local
single-user tool). On loopback that is the same trust boundary as editing `.env`. It stops
being so the moment the application is exposed to a network, which is the point at which A5
stops being optional.
