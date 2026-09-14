# Migration

*What happens to what already exists when V1.0 lands. Specification for the transition.*

**What exists, read from the database on 14 September 2026:** 6 jobs, 3 approved reports, 3,417
calculations, 46,744 financial facts, 85 source documents, and 139 MB of artefacts across 832
files. No theses, no decisions, no watchlist entries, no reviews.

**Why this matters more than it looks.** That data is the delivery plan's entire £0 test corpus —
every "re-render the five stored runs and assert the change" acceptance criterion depends on it
being intact and reachable after the schema moves. A migration that strands it does not merely
lose history; it removes the ability to prove most of V1.0 without paying for new runs.

---

## 1 · The rule

**Nothing is rewritten and nothing is deleted.** Every migration below adds columns, adds rows,
or adds tables. No existing report is altered, no artefact is re-hashed, no calculation is
recomputed in place. A report approved in September 2026 must still replay, byte for byte, after
every migration in this document has run.

That is not caution for its own sake: `reports.immutable` is a promise the platform made, and a
migration that edits an immutable row breaks the only thing that makes the archive worth having.

## 2 · Schema migrations, in order

Each is additive. Each is reversible except where noted.

| # | Migration | Effect on existing rows | Reversible |
|---|---|---|---|
| M1 | `reports.superseded_by`, `superseded_at`, `supersession_reason` | All null — every existing report is current | Yes |
| M2 | The partial unique index, one current report per company | **Must be checked first.** See §3 | Yes |
| M3 | `jobs.refreshes_report_id`, `refresh_kind` | All existing jobs become `refresh_kind = 'full'` | Yes |
| M4 | `report_changes` table | Empty | Yes |
| M5 | `finding_kind` gains `price_move` | No existing findings | **No** — Postgres cannot remove an enum value |
| M6 | `findings.security_id`, `dismissed_at`, `dismissed_reason` | All null | Yes |
| M7 | `watchlist_entries` gains cadence, threshold, window, check timestamps | No existing entries | Yes |
| M8 | `questions` table | Empty | Yes |
| M9 | `reports.workbook_artefact_id` | All null — no existing report has a workbook | Yes |

**M5 is the one-way door**, and it is a single enum value. Worth knowing, not worth avoiding.

## 3 · The one migration that can fail

**M2, the partial unique index**, asserts that a company has at most one current report. Three
reports exist across three companies, so it will pass — but it must be *checked* rather than
assumed, because the assertion is exactly the invariant nothing enforced before:

```sql
SELECT company_id, count(*) FROM reports
WHERE immutable AND superseded_by IS NULL AND superseded_at IS NULL
GROUP BY company_id HAVING count(*) > 1;
```

**If it returns rows**, the migration stops and the operator chooses which is current per company.
It is not resolvable automatically: "the newest" is a guess about intent, and intent is the
operator's.

## 4 · Backfill

### 4.1 What is backfilled automatically

| Record | From what | Result |
|---|---|---|
| **Watchlist entries** | Every company with an approved report | One entry per company, `why = "researched {date}"`, default cadence, `last_checked_at = null` |
| **Refresh lineage** | — | Existing jobs marked `refresh_kind = 'full'`; `refreshes_report_id` stays null |
| **Report currency** | — | Every existing report is current. Where two exist for one company (§3), the operator chooses |

That is all. Three tables gain rows; nothing else is inferred.

### 4.2 What is deliberately not backfilled

**No theses are invented.** The obvious temptation is to generate a thesis from each report's
conclusions so the new portfolio page has something to show. **Do not.** A thesis is what the
operator believes, recorded by them, with a `held_by` and a `basis`; a thesis the system wrote on
their behalf is a fabricated judgement in a table whose entire purpose is to hold real ones. The
portfolio page showing `no thesis` against a real holding is correct, useful and honest — it is
the top-sorted row precisely because it is a real gap.

**No decisions are invented** from transactions, for the same reason and more strongly: a
decision carries a reason, and nobody can reconstruct one after the fact.

**No premises are invented** from a report's own claims.

### 4.3 What the operator is asked to do afterwards

On first run of V1.0, Today's *Worth doing* band carries, earned by the backfill:

- *"Three companies have reports but no thesis."*
- *"Your book has no positions recorded."* — if `transactions` is empty.

That is the migration's entire user-facing surface: two suggestions, both honest, neither
blocking.

## 5 · The artefact store

**Untouched.** 832 files, 139 MB, content-addressed. No migration moves, renames, re-hashes or
prunes an artefact. `aer verify-artefacts` must report the same count and the same digests after
every migration in §2.

**One addition:** workbooks generated for existing reports (if the operator asks) are new
artefacts alongside the old, never replacing a PDF.

## 6 · Point-in-time removal, which is not a data migration

F1 removes enforcement, a mode and two metrics. It does **not** remove data:

- `reports.as_of_date` **stays**. It records the date the run was made as-at, which is history and
  remains true.
- Every source document's **retrieval timestamp stays**, and F14 depends on it.
- Existing evaluation rows for `temporal_compliance` and `look_ahead_recall` **stay**. They are
  the record of measurements taken; deleting them would rewrite the audit's own evidence.

The metrics stop being *computed*. What was computed stays computed.

## 7 · Order of operations

```
1  Back up, and prove the backup restores          ← the open item on the Platform page
2  Run the §3 check                                ← must pass, or stop and ask
3  M1 → M9, in order, each with the suite green
4  Backfill §4.1
5  aer verify-artefacts · aer verify-audit · replay all five runs
6  Re-render the five stored reports and diff against their stored markdown
```

**Step 6 is the real acceptance test.** If a stored report re-renders differently after the
migrations, something in the additive-only rule was violated, and it is far easier to find at
step 6 than after forty sessions of feature work.

## 8 · Rollback

Every migration except M5 is `alembic downgrade` clean, because all are additive. **The backfill
is not automatically reversible** — dropping the backfilled watchlist entries is a delete, and
the operator may have edited them by then. Rollback of the backfill is therefore manual and
documented, which is acceptable because the backfill writes 3 rows to one table.

**If a rollback is needed after the operator has written a real thesis or decision**, do not roll
back. Fix forward. Judgements are the one kind of record in this system that cannot be
reconstructed.
