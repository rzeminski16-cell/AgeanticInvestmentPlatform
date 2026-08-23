# UK inline XBRL

A UK filing is one XHTML document that is **both** the readable annual report and the
machine-readable data. `aer.extract.html` produces the readable half — `sniff` classifies inline
XBRL as HTML precisely so that works — and `aer.extract.ixbrl` produces the facts.

## arelle runs offline, and that is a control

An iXBRL document names its taxonomy by URL. **arelle's default is to go and fetch it.**

That would be a component other than `aer.fetch` making an outbound request, driven by a URL
inside an untrusted document, past every allowlist, rate limit and SSRF check in the platform.
It is the exact shape of the thing this architecture exists to prevent.

`extract_ixbrl` sets `webCache.workOffline = True` before loading anything, and a test asserts
that loading a document naming a remote schema opens **no socket**. Removing that line makes the
test fail with arelle reporting `Attempt to load network entity` — verified, not assumed.

## What offline costs, precisely

Without the schema, arelle cannot resolve concepts. It cannot tell you an element's data type,
its balance sign, or its place in a calculation tree.

It still gives you everything a fact *is*:

| Available offline | Needs the taxonomy |
|---|---|
| The tag's qualified name | The element's data type |
| The value, with the document's own `scale` applied | The balance sign (debit/credit) |
| `decimals` | Calculation relationships |
| The period, and whether it is an instant | Human-readable labels |
| The entity scheme and identifier | Presentation ordering |
| The unit | |

The missing half is not something this platform was going to trust a taxonomy for anyway: units
are checked by `aer.calc.units` and the meaning of a tag comes from the concept vocabulary in
`aer.core.concepts`, which is a decision recorded in this repository rather than one downloaded.

## Two things that are easy to get wrong

**arelle reports an exclusive period end.** A year ending 30 June comes back as 1 July, because
that is how XBRL defines a duration internally. One day is subtracted so the stored date is the
one printed in the accounts. Left uncorrected it moves *every* UK fiscal year end by a day — an
error that survives review because it looks almost right.

**The document's `scale` is already applied by arelle.** A UK report states figures in thousands
and tags `scale="3"`; `fact.value` is in pounds. Applying it a second time is a thousandfold
error that looks entirely plausible on a page.

## The confirmation gate

`docs/archive/PLAN.md` names UK taxonomy variability as this phase's main risk. The mitigation is a gate,
and what raises it matters.

**Not "arelle could not resolve the schema"** — offline, that is true of every document, and a
verdict that fires every time is a badge nobody reads.

**Whether the filing's tags map to canonical concepts.** `ifrs-full:Revenue` does.
`acme:AdjustedEBITDAPreExceptionalItems` does not, because it is an element the filer invented.
An extraction carrying unmapped tags is `needs_confirmation`, and those facts do not become
evidence until a person says which concept, if any, each one means.

The gate is on **tags, not on a count or a ratio**: one extension carrying the company's headline
profit measure matters and forty carrying segment breakdowns nobody asked for do not, and only a
person can tell which. An extension does not poison the filing — the standard tags beside it are
still standard, and `mapped_facts` are usable without a decision.

Unmapped facts are **kept under their raw tag**, never dropped. Discarding them would lose real
data and leave no trace that a number was there at all.

## Which taxonomy

LSE-listed issuers apply IFRS as adopted for use in the UK, so their inline XBRL is tagged from
`ifrs-full`. The FRC's `uk-*` taxonomies are for the FRS 101/102 regime that smaller and private
companies use; a short list of the most common `uk-core` spellings is mapped anyway, because a UK
filer occasionally tags a statutory line from them alongside the IFRS ones.

Where IFRS names a concept differently from US GAAP rather than merely spelling it differently,
the alias maps to the canonical name whose **definition** matches rather than the one whose words
look closest. `ProfitLossFromOperatingActivities` is operating income; `FinanceCosts` is the
interest line. Getting one of those wrong puts a real number on the wrong line, which is worse
than leaving it unmapped and visible.

## Licensing

`arelle-release` is Apache-2.0, and its whole dependency tree is permissive — checked from
installed metadata, not assumed. See ADR 0020 for why that check is now routine.

## Testing

`tests/ixbrl_fixtures.py` builds filings by hand so a truth set can state what is in them: one
whose every tag is from a shared taxonomy, one carrying a filer extension, one that names a
remote taxonomy and nothing else, and one that is a UK annual report with no tagging at all.
