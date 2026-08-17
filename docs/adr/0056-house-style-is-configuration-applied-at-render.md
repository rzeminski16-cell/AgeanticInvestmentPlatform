# 0056 — House style is configuration, applied at render

Date: 2026-08-17
Status: accepted

## Context

The first full report mixed "$39.5 billion" with "39,544 USD millions" on facing pages,
printed ratios as `0.462` in one table and "46.2%" in the neighbouring note, and rendered
raw integers — `11729000000 USD` — into prose. Each writer made its own presentation
choices because nothing owned them: there was no single place where "how does this note
write a large number" was decided.

At the same time, the remediation plan needs a deterministic display formatter (R1) that
rewrites values at render. A formatter needs a style to implement, and building the
formatter first would mean hard-coding the style into it, then unpicking it later.

## Decision

Presentation is configuration. A frozen `HouseStyle` model on `Settings` decides, once:

- **how prose scales money** — `auto` (switch to billions at a configurable threshold,
  default $1bn) or `millions` (never scale). Tables always render in millions, because a
  column only lines up in one scale;
- **the date format**, as a strftime pattern validated at configuration time by actually
  formatting a probe date;
- **the register** — impersonal ("the evidence supports") or first-person plural
  ("we estimate").

The style is operator-editable without a restart through the existing
`settings_overrides` mechanism (ADR 0050): one `house_style` key on the `OVERRIDABLE`
allowlist, stored as JSONB, applied by `effective_settings` at run start. A partial
object overrides only the fields it names — unlike the routing table there is no merge
validator, because every `HouseStyle` field carries its own default.

**The style is applied at render and in the writers' style instructions, never to stored
values.** A calculation row holds the exact `Decimal` it always held; the style decides
only what the reader sees. This is the boundary invariant 3 draws, and the display
formatter (R1) is required to observe it: formatting is a projection of the stored value,
not a replacement for it.

## Consequences

- Changing the note's presentation is a settings edit, not a code change, and applies to
  runs that start after it — the same semantics as every other override.
- The R1 formatter, the section writers' style prompt and any future export surface read
  one object rather than each holding an opinion.
- A restyled report and its predecessor can disagree about display while agreeing about
  every stored figure, which is exactly the audit property the platform promises.
- The strftime pattern is trusted as far as "it renders a non-empty date" — an operator
  can still choose an ugly format, and the platform will not argue taste.
