# Getting started

From a clean machine to a running platform. Budget about half an hour, most of it waiting
for downloads.

> **This is a personal research tool. It is not regulated investment advice.**

---

## What you need

- **Python 3.12**
- [**uv**](https://docs.astral.sh/uv/) for dependency management
- **Docker Desktop**, for PostgreSQL and Redis
- Optionally [**just**](https://github.com/casey/just), a task runner. Every recipe in the
  `justfile` is a one-line `uv run …` command, so you can work without it.

PDF rendering uses [WeasyPrint](https://weasyprint.org/), which needs a native stack
(Pango, cairo, GDK-PixBuf) on the machine:

- **Windows** — install the GTK runtime.
- **Debian/Ubuntu** — `libpango-1.0-0 libpangoft2-1.0-0 libcairo2 libgdk-pixbuf-2.0-0`.

Validate that before you need it. It is the one dependency that can surprise you late.
Everything else — matplotlib for charts, pikepdf for the PDF finishing pass — arrives with
`uv sync`.

## Install

```powershell
# 1. Install uv (once)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

# 2. Clone
git clone https://github.com/rzeminski16-cell/AgeianticEquityResearchPlatform.git
cd AgeianticEquityResearchPlatform

# 3. Pinned Python and dependencies
uv python install 3.12
uv sync --all-groups

# 4. Git hooks
uv run pre-commit install

# 5. Configure
copy .env.example .env

# 6. Postgres and Redis
docker compose up -d
docker compose ps          # both should report healthy

# 7. Schema and your user
uv run alembic upgrade head
uv run aer seed-user --email you@example.com

# 8. Check it works
uv run pytest
uv run ruff check .
uv run mypy
```

macOS and Linux are identical apart from the installer and `cp` instead of `copy`:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

## Configure

Settings come from `AER_*` environment variables or from `.env`. Every one is documented in
`.env.example`, which is complete enough that copying it and filling in a single value
gives a working configuration.

**`AER_HTTP_USER_AGENT` is the only required setting.** It has no default on purpose: the
SEC requires a descriptive User-Agent identifying the operator as a condition of using its
APIs, and a shared placeholder would get everybody using it blocked together. Set it to a
real name and a contact address you actually monitor.

```
AER_HTTP_USER_AGENT=Jane Smith jane@example.com
```

Two more worth knowing about:

- **`AER_SECRET_KEY`** signs CSRF tokens. Leave it blank locally and one is generated per
  process — the only consequence is that a form left open across a restart needs reloading.
  It becomes **required** when `AER_APP_ENV=production`.
- **API keys are deliberately not required at startup.** A missing key fails at the point
  the provider is used, naming the variable to set, so you are never blocked on credentials
  for a service you have not reached yet.

Inspect the effective configuration at any time with `just config`. Secrets render masked.

## Run it

A run executes in a background worker, so **two processes must be up**:

```bash
uv run aer serve                        # the GUI and API, on 127.0.0.1:8000
uv run arq aer.worker.WorkerSettings    # the worker that executes runs
```

Or, with `just`:

```bash
just dev      # web server with auto-reload
just worker   # the worker
```

Then open <http://127.0.0.1:8000>.

**The worker's log is the terminal running it.** There is no log file and no worker
container — `arq` writes structured JSON to stdout, so whatever started it owns the output.
A run's failure appears there first and in full, with the traceback the console can only
summarise. To keep a copy worth pasting into a bug report:

```bash
just worker 2>&1 | tee var/worker.log                    # bash, zsh
just worker 2>&1 | Tee-Object -FilePath var\worker.log   # PowerShell
```

`var/` is git-ignored, so nothing captured this way can be committed by accident.

## Infrastructure commands

```bash
just up        # start Postgres and Redis
just health    # pg_isready + redis ping
just psql      # a psql shell on the dev database
just down      # stop, keeping data
just down-hard # stop and DELETE all data
```

Every published port binds to `127.0.0.1` only. Docker bypasses host firewalls when
publishing ports, so a plain `5432:5432` would expose your database to whatever network
you are on — a hotel or coffee-shop wifi included. Do not remove the loopback prefixes.

## Everyday commands

| Command | What it does |
|---|---|
| `just serve` / `just dev` | Web server, without / with auto-reload |
| `just worker` | The background worker that executes runs |
| `just seed-user you@example.com` | Create the local user (idempotent) |
| `just reset-research` | Delete every research request and everything derived from one |
| `just backup var/backups/today` | Database and artefact store into one verified directory |
| `just verify-backup <dir>` | Re-hash a backup against its manifest; needs no database |
| `just restore <dir>` | Put a backup back (destructive; verifies first, then asks) |
| `just verify-artefacts` | Re-read every archived artefact; check it still hashes to its name |
| `just verify-audit` | Walk the audit log; check every record still links to the one before |
| `just replay-run <job-id>` | Re-derive a run from its own record |
| `just gc-artefacts` | Report archived bytes nothing points at (`--delete` to remove) |
| `just purge-licensed` | Delete every payload from a licensed feed, when its terms require |
| `just lint` / `just fix` | Lint and check formatting / apply fixes |
| `just typecheck` | Run mypy |
| `just test` | The suite, excluding browser tests |
| `just ci` | Everything CI runs, in the same order |

`/costs` in the web interface shows what the platform has spent, per role, with the
prompt-cache hit rate.

## Your first run

Commission something small and familiar first — a large US filer with a long, clean filing
history is the gentlest start. Then read
[**running a report**](running-a-report.md), which walks the gates in order and says what
each approval commits you to.

If something goes wrong, [**troubleshooting**](troubleshooting.md) covers the failures that
are expected rather than exceptional.
