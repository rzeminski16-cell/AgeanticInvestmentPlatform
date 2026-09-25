# Bringing it up on your own Windows machine

*The last of V1.0's four finish-line conditions ([ROADMAP](../plan/ROADMAP.md), "What to do
next", item 4): the platform runs on your own machine with the corpus restored, and your own
timed first run meets the three bars nobody else can time. This page is the checklist for that
day, in order. [Getting started](getting-started.md) has the detail behind each install step.*

> **This is a personal research tool. It is not regulated investment advice.**

---

## What to have to hand

- **The backup from 25 September 2026**: the directory holding the database dump, the artefact
  store and the manifest. It is the corpus, and restoring it means you do not buy it again.
  Use the V1.0 one, `backup-2026-09-25-v1`, sent as three zips
  (`aer-backup-2026-09-25-v1-part1.zip` to `part3.zip`). Extract all three into the same
  folder and they rebuild one directory. It holds everything the earlier backup did, plus the
  re-measurement's four approved reports, and it was taken at this code's migration.
- **Your keys.** They go into `.env` and nowhere else — never into a chat, a commit or a
  screenshot:

  | Variable | What it is for |
  |---|---|
  | `AER_HTTP_USER_AGENT` | Your name and an address you read. The SEC requires it; it is the only setting with no default |
  | `AER_ANTHROPIC_API_KEY` | Every model call |
  | `AER_EODHD_API_KEY` | Prices; the market capitalisation and the beta are computed from them |
  | `AER_FRED_API_KEY` | The risk-free rate |
  | `AER_COMPANIES_HOUSE_API_KEY` | UK filers |

- **About an hour**, most of it downloads, before the timed run.

## 1. Install

- [ ] **Docker Desktop**, with the WSL 2 backend, running.
- [ ] **Git**.
- [ ] **uv**:
      `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`.
- [ ] **The GTK runtime**, for the PDF renderer. It is the one dependency that surprises people
      late, so prove it now, in step 3.
- [ ] **The PostgreSQL 16 command-line tools.** The database runs inside Docker, but backup and
      restore call `pg_dump` and `pg_restore` on your machine. From the PostgreSQL 16 installer,
      tick only *Command Line Tools*, then add `C:\Program Files\PostgreSQL\16\bin` to `PATH`.
      Open a new terminal and check that `pg_restore --version` prints 16.
- [ ] Optionally **just** (`winget install Casey.Just`). Every recipe is one `uv run …` line, so
      nothing below needs it.

## 2. Clone and configure

```powershell
git clone https://github.com/rzeminski16-cell/AgeanticInvestmentPlatform.git
cd AgeanticInvestmentPlatform
uv python install 3.12
uv sync --all-groups
copy .env.example .env      # then fill in the five variables above
docker compose up -d
docker compose ps           # both healthy
```

## 3. Prove the PDF renderer before you need it

```powershell
mkdir var -ErrorAction SilentlyContinue
uv run python -c "from weasyprint import HTML; HTML(string='<p>ok</p>').write_pdf('var/check.pdf')"
```

- [ ] `var\check.pdf` exists and opens. A few `GLib-GIO-WARNING` lines on the way are the GTK
      stack talking and are harmless. An error naming a missing library means the GTK runtime
      is not on `PATH`.

## 4. Restore the corpus

```powershell
uv run aer verify-backup --from <the backup directory>
uv run aer restore --from <the backup directory>
uv run alembic upgrade head
uv run aer verify-artefacts
uv run aer verify-audit
```

- [ ] `verify-backup` reports every file matching its manifest. Restore checks again before it
      touches anything, and refuses a backup that does not check out.
- [ ] `restore` asks before it drops and rebuilds the database. Say yes: the database is empty.
- [ ] `alembic upgrade head` moves the restored schema to this code's. The V1.0 backup was
      taken at migration 0090, so on this code it has nothing to do. An earlier backup needs
      it: the upgrade is what brings the report archive's workbook column and the two cases'
      section contracts.
- [ ] `verify-artefacts` and `verify-audit` are both clean.

Your user comes back with the database, so there is no `seed-user` step.

## 5. Check a paid run could survive

Two terminals, both left running:

```powershell
uv run aer serve                         # the web interface, on http://127.0.0.1:8000
uv run arq aer.worker.WorkerSettings     # the worker that executes runs
```

Then, in a third:

```powershell
uv run aer preflight
```

- [ ] Every row of `preflight` reads ready. It checks the keys, the caps, the worker and every
      dependency a paid run needs, and it costs nothing.
- [ ] <http://127.0.0.1:8000/reports> lists the restored reports, and one opens with its
      footnotes resolving.

## 6. The acceptance: your own timed first run

Choose a company the corpus has not researched: a large US filer with a long, clean filing
history is the fairest first test. Time three things, and write the numbers down as you go:

| The bar | How to time it | Passes at |
|---|---|---|
| Commission and approve a first report **without opening the documentation** | Yes or no, and where you reached for it | Never reached for it |
| Walk **three figures** to their source in the reader | A stopwatch per figure, from reading the number to seeing the filing's excerpt | Each under **30 seconds** |
| Your **attention** across every gate | Minutes per gate, reading and deciding, not waiting for the worker | Under **15 minutes** in total |

Also note the run's cost from `/costs` (the bar is under £8), and anything that stopped you or
made you guess. Hand the notes back and they go into the roadmap as the finish line's fourth
condition, measured, whatever they say.

## 7. When development is finished: rotate the Companies House key

The Companies House key reached Amazon S3 on 18 September 2026 (ROADMAP §3.19 item 23), and you
chose to rotate it at the end of development rather than mid-way. When that day comes:

- [ ] In the Companies House developer hub, create a new key for the application and delete the
      old one.
- [ ] Put the new key in `.env` as `AER_COMPANIES_HOUSE_API_KEY`, restart the worker, and run
      `uv run aer preflight` again.

---

**If something goes wrong:** [troubleshooting](troubleshooting.md) covers the failures that are
expected rather than exceptional, and the worker's terminal carries the full traceback of any
run that fails.
