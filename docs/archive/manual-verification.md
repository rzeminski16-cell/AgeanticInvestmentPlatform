# Manual verification

> **Superseded.** This is the phase-era verification walkthrough, kept as a record. The
> current sheet is [`../developers/testing-by-hand.md`](../developers/testing-by-hand.md),
> which covers the merged platform — both working tools, the shell, and the current test
> counts. Branch names, test counts and surfaces below pre-date the 2026-08-23 merge.

Everything below is something the automated suite cannot prove on its own — because it
needs Docker, or a real browser you are looking at, or the real SEC, or a real model call
that costs real money.

Work through it in order. Each check says what to run, what you should see, and — where it
matters — **what would mean something is wrong**. A check that "seems fine" is not a pass;
compare against the stated expectation.

Commands are PowerShell. On macOS or Linux they are identical apart from `copy` → `cp`.

**If this is your first time running the system, start at section 0** — it installs the
tools and includes what to do when Docker Desktop will not start, which is where most first
attempts stall. If you already have Docker and uv working, start at section 1.

**Cost warning.** Sections 0–6 and 9–12 spend nothing. **Section 7 makes one real model
call and costs a few pence** (one Opus 5 planner call, typically £0.03–£0.06). Section 8
depends on it. Nothing else in this document reaches a paid API.

A note on what has and has not been checked: everything from section 2 onward has been run
against a real PostgreSQL, a real Redis and real files on disk. **Sections 0 and 1 have
not** — the environment this was built in has no Docker and no Windows. They are written
from the compose file and from Docker's documented behaviour, so treat any discrepancy as
a defect in these instructions and tell me.

---

## 0. Before anything else: install the tools

Skip any you already have. Each step ends in a command that proves it worked — run it, and
do not move on until it does.

### 0.1 Docker Desktop

Docker runs PostgreSQL and Redis for you, so you never install a database on Windows
directly. On Windows it runs them inside WSL 2 (a Linux environment Windows ships with).

**a. Enable WSL 2.** Open **PowerShell as Administrator** (right-click Start → Terminal
(Admin)) and run:

```powershell
wsl --install
```

If it says WSL is already installed, that is fine. If it installs something, **reboot**.

If it fails with a virtualisation error, virtualisation is disabled in your BIOS/UEFI.
You will need to reboot into BIOS settings and enable **Intel VT-x** or **AMD-V**. Every
motherboard names it differently; search for your model plus "enable virtualisation".

**b. Install Docker Desktop.** Download from
<https://www.docker.com/products/docker-desktop/> and run the installer. Accept the
default "Use WSL 2 instead of Hyper-V". Reboot if asked.

**c. Start it.** Launch Docker Desktop from the Start menu. Wait until the whale icon in
the system tray (bottom-right, possibly under the `^` arrow) stops animating and the app
window says **Engine running**.

This matters more than it sounds: **every `docker` command fails until Docker Desktop is
running**, and the error does not say so clearly. If you reboot, you have to start it
again unless you enabled "Start Docker Desktop when you sign in" in its settings.

**d. Prove it works.** In a *normal* (non-admin) PowerShell window:

```powershell
docker version
```

**Expect:** two blocks, `Client:` and `Server: Docker Desktop`, both with version numbers.

**Wrong:** anything mentioning `The system cannot find the file specified`, for example:

```
failed to connect to the docker API at npipe:////./pipe/dockerDesktopLinuxEngine;
check if the path is correct and if the daemon is running
```

That is Docker Desktop's Linux engine not running. `docker` and `docker compose` are
installed correctly; there is simply nothing for them to talk to. See **0.1e** below — it
is the single most common first-run problem and none of it is a fault in this project.

Then check Compose, which is a separate tool bundled with Docker Desktop:

```powershell
docker compose version
```

**Expect:** `Docker Compose version v2.x` or higher. Note the space: `docker compose`, not
`docker-compose`. The hyphenated form is the old standalone tool and this project does not
use it.

### 0.1e When the engine will not start

Work down the list. Re-run `docker version` after each step and stop when the `Server:`
block appears.

**1. Is Docker Desktop actually running?** Not installed — *running*. Look for the whale
icon in the system tray (bottom-right, possibly hidden under the `^` arrow). If it is not
there, launch Docker Desktop from the Start menu and wait. First start after installation
can take two or three minutes.

Open the Docker Desktop window and look at the bottom-left corner. You want **Engine
running** in green. `Starting…` means wait. Anything red means read the message.

**2. Are you in Windows-containers mode?** The pipe name in your error,
`dockerDesktopLinuxEngine`, is the Linux engine. If Docker Desktop was switched to Windows
containers, that pipe never gets created. Right-click the whale icon: if the menu offers
**"Switch to Linux containers…"**, you are in the wrong mode — click it and wait.

**3. Is WSL 2 healthy?** In PowerShell:

```powershell
wsl --status
wsl --list --verbose
```

**Expect:** a default version of 2, and `docker-desktop` listed with `VERSION 2`.

If WSL is missing or old:

```powershell
wsl --update
```

Then restart Docker Desktop.

**4. Give WSL a clean restart.** This fixes a surprising proportion of cases where the
engine hangs in `Starting…`. Quit Docker Desktop from the tray icon first, then:

```powershell
wsl --shutdown
```

Wait ten seconds, then start Docker Desktop again.

**5. Is virtualisation enabled?** Open Task Manager → **Performance** → **CPU** and look
for **Virtualization: Enabled**. If it says Disabled, WSL 2 cannot run and Docker never
will. Reboot into your BIOS/UEFI and enable **Intel VT-x** or **AMD-V** — the setting is
named differently on every board, so search for your motherboard or laptop model plus
"enable virtualisation".

**6. Read what Docker itself says.** Docker Desktop → the bug icon → **Troubleshoot**, or
its log at `%LOCALAPPDATA%\Docker\log.txt`. By this point the problem is specific to your
machine and the log will name it.

**7. Last resort:** Docker Desktop → Settings → Troubleshoot → **Reset to factory
defaults**. This deletes all containers, images and volumes — which is harmless here,
because nothing of yours exists in Docker yet.

**Do not continue to section 1 until `docker version` prints a `Server:` block.** Every
command in that section talks to the engine, and each will fail with the same error.

### 0.2 Git

```powershell
git --version
```

If that fails, install from <https://git-scm.com/download/win> and accept the defaults.

### 0.3 uv

`uv` installs Python, resolves dependencies and runs commands inside the project's
environment. Every command in this document that starts `uv run` needs it.

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Watch the output. It should end by saying it installed `uv` and `uvx` and added a
directory to your `PATH`. If it printed an error instead, see 0.3a.

**Now close this PowerShell window and open a new one.** This is not optional and not
superstition: the installer edits your user `PATH` in the registry, and Windows hands a
process its environment when the process *starts*. An already-open window keeps the `PATH`
it was born with, forever. Reopening is what picks up the change.

```powershell
uv --version
```

**Expect:** something like `uv 0.9.x`.

### 0.3a When `uv` is not recognized

```
uv : The term 'uv' is not recognized as the name of a cmdlet, function, script file,
or operable program.
```

This means Windows cannot find `uv` on your `PATH`. It does **not** necessarily mean the
install failed. Work down the list.

**1. Did you open a new window?** By far the most common cause. Close every PowerShell
window, open a fresh one, and try again.

If you would rather not reopen — or you did and it still fails — pull the updated `PATH`
into the current window:

```powershell
$env:Path = [Environment]::GetEnvironmentVariable("Path","User") + ";" + [Environment]::GetEnvironmentVariable("Path","Machine")
uv --version
```

If that works, the install was fine all along and only the window was stale. Note that
this fixes the current window only; new windows get it permanently.

**2. Is the binary actually there?** The installer's location has changed between
versions, so check both:

```powershell
Test-Path "$HOME\.local\bin\uv.exe"
Test-Path "$HOME\.cargo\bin\uv.exe"
```

If either says `True`, it installed correctly and this is purely a `PATH` problem. Confirm
by running it by its full path:

```powershell
& "$HOME\.local\bin\uv.exe" --version
```

To fix the `PATH` permanently, replacing `.local\bin` with whichever directory was `True`:

```powershell
[Environment]::SetEnvironmentVariable(
  "Path",
  [Environment]::GetEnvironmentVariable("Path","User") + ";$HOME\.local\bin",
  "User"
)
```

Then open a new window and check `uv --version`.

**3. If both said `False`, it never installed.** Scroll back through the installer output
and read the error. Common causes:

- **Execution policy or antivirus** blocked the script. The `-ExecutionPolicy ByPass` in
  the command handles the usual case; corporate machine-level policy can still refuse.
- **No network, or a proxy** intercepting `astral.sh`.

Use a different installation method instead — any of these gives you the same tool:

```powershell
winget install --id=astral-sh.uv -e
```

or download the `uv-x86_64-pc-windows-msvc.zip` from
<https://github.com/astral-sh/uv/releases>, unzip it somewhere permanent such as
`C:\tools\uv`, and add that folder to your `PATH` with the `SetEnvironmentVariable`
command above.

Then open a new window and check `uv --version`.

**4. Check you are not in a strange shell.** This document assumes **PowerShell**. If your
prompt is `C:\>` you are in the old Command Prompt — most commands here still work, but
`$HOME` and `Test-Path` do not. Open "Windows PowerShell" or "Terminal" from the Start
menu.

**Do not continue until `uv --version` prints a version.** Everything from section 2
onward runs through `uv`.

### 0.4 The repository

```powershell
cd $HOME
git clone https://github.com/rzeminski16-cell/AgeianticEquityResearchPlatform.git
cd AgeianticEquityResearchPlatform
git checkout claude/equity-research-platform-uf2mql
```

**Every command in the rest of this document must be run from this directory.** Docker
Compose finds `docker-compose.yml` by looking in the current directory; from anywhere else
it will tell you no configuration file was found. Check with:

```powershell
Get-Location          # -> ...\AgeianticEquityResearchPlatform
Test-Path docker-compose.yml    # -> True
```

---

## 1. Local infrastructure

This is the one acceptance criterion from Task 2 that has never been verified, because
image pulls are blocked in the sandbox this was built in. So this section is genuinely a
test, not a formality — if something is wrong with the compose file, it will show up here.

### 1.1 Create your configuration first

Do this **before** starting the containers, and understand why.

```powershell
copy .env.example .env
```

Compose reads `.env` from the current directory and uses it for the database name, user
and password. PostgreSQL applies those **only when it first creates its data volume**.
If you start the containers now and change the password later, the container keeps the old
one and the application gets an authentication error that looks like a bug in the
application. Getting out of that means `docker compose down -v`, which deletes the data.

For this first run, leave the credentials at their defaults. The only value you need to
set is `AER_HTTP_USER_AGENT`, and that is section 2 — the containers do not care about it.

If you ever *do* change `AER_POSTGRES_PASSWORD`, you must change the password inside
`AER_DATABASE_URL` to match. They are two separate settings that have to agree.

### 1.2 Start the containers

```powershell
docker compose up -d
```

**The first run downloads about 150 MB** of images and takes a few minutes. You will see
progress bars for `postgres:16-alpine` and `redis:7-alpine`. Subsequent runs start in
about two seconds.

**Expect**, eventually:

```
[+] Running 4/4
 ✔ Network aer_default        Created
 ✔ Volume "aer_postgres_data" Created
 ✔ Volume "aer_redis_data"    Created
 ✔ Container aer-postgres     Started
 ✔ Container aer-redis        Started
```

The exact layout varies by Compose version; the container names `aer-postgres` and
`aer-redis` do not.

**Wrong:** `port is already allocated`. Something else on your machine is using 5432 or
6379 — most likely a PostgreSQL you installed directly at some point. Either stop it, or
set `AER_POSTGRES_PORT=5433` in `.env` and change the port in `AER_DATABASE_URL` to match.

### 1.3 Wait for healthy, then check

`Started` is not `healthy`. The containers are up before the databases inside them are
ready to answer, which is exactly the kind of thing that makes a first run look broken.
The health checks poll every 5 seconds and give PostgreSQL a 10-second grace period, so
give it **about 20 seconds**, then:

```powershell
docker compose ps
```

**Expect:** two rows, `postgres` and `redis`, both with status `Up ... (healthy)`.

**If it says `(health: starting)`** — wait another 15 seconds and run it again. That is
normal.

**If it says `(unhealthy)`** after a minute, read the logs:

```powershell
docker compose logs postgres
```

### 1.4 Confirm the ports are bound to loopback only

This is a security check, not a connectivity check, and it is the reason the compose file
is written the way it is. Docker publishes ports by writing firewall rules directly — it
bypasses Windows Firewall — so a port published without an explicit address is reachable
by **anything on your network**, including whatever else is on a hotel or café wifi.

```powershell
docker compose port postgres 5432
docker compose port redis 6379
```

**Expect exactly:**

```
127.0.0.1:5432
127.0.0.1:6379
```

**Wrong:** `0.0.0.0:5432`. That is your database exposed to the network. If you ever see
it, the loopback prefixes have been removed from `docker-compose.yml`.

You can see the same thing in `docker compose ps` under `PORTS`, though the formatting
varies between Docker versions — `docker compose port` is the version-independent answer.

### 1.5 Confirm the databases actually answer

Up and healthy is Docker's opinion. Ask the services directly:

```powershell
docker compose exec postgres pg_isready -U aer -d aer
```

**Expect:** `/var/run/postgresql:5432 - accepting connections`

```powershell
docker compose exec redis redis-cli ping
```

**Expect:** `PONG`

**Wrong:** `service "postgres" is not running`. The container stopped after starting —
`docker compose logs postgres` will say why.

### 1.6 Confirm the object store did *not* start

MinIO sits behind a Compose profile, so it stays out of the way until something needs the
S3 code path. Nothing in Phase 1 does — the artefact store is your local disk.

```powershell
docker compose ps minio
```

**Expect:** a header row and nothing under it. If a `aer-minio` container is running, the
profile is not doing its job.

Now start it deliberately, confirm it works, and stop it again:

```powershell
docker compose --profile objectstore up -d
docker compose ps minio
```

**Expect:** `aer-minio` running. Its console is at <http://127.0.0.1:9001> if you want to
look — user `aer_minio`, password `aer_local_dev_minio`, both local-only defaults.

```powershell
docker compose --profile objectstore stop minio
docker compose --profile objectstore rm -f minio
```

**Expect:** `aer-postgres` and `aer-redis` still running afterwards. Check with
`docker compose ps`.

### 1.7 Confirm the data survives a restart

The whole point of the named volumes. This takes ten seconds and catches a
misconfiguration that would otherwise only show up when you lose a report.

```powershell
docker compose exec postgres psql -U aer -d aer -c "CREATE TABLE restart_probe (note text); INSERT INTO restart_probe VALUES ('still here');"
docker compose down
docker compose up -d
```

Wait for healthy, then:

```powershell
docker compose exec postgres psql -U aer -d aer -c "SELECT * FROM restart_probe;"
```

**Expect:** `still here`.

**Wrong:** `relation "restart_probe" does not exist`. That means the data volume is not
persisting, and every report you ever generate would vanish on the next restart.

Clean up the probe:

```powershell
docker compose exec postgres psql -U aer -d aer -c "DROP TABLE restart_probe;"
```

### 1.8 Know how to stop and how to reset

```powershell
docker compose down       # stop the containers, KEEP the data
docker compose down -v    # stop AND DELETE the data volumes
```

`down -v` is the reset button for when the database is in a state you would rather not
reason about. It is also unrecoverable, so it is worth knowing which one you are typing.

Leave the containers **running** — section 2 needs them.

**Section 1 passes when:** `docker compose ps` shows postgres and redis healthy,
`docker compose port` shows both bound to `127.0.0.1`, both services answer directly,
minio is absent unless asked for, and data survived a `down`/`up`.

---

## 2. Setup from a clean checkout

The point of this one is that `.env.example` is sufficient — that a new machine needs one
value filled in, not a scavenger hunt.

You copied `.env` in step 1.1. Now open it in an editor and set **one** value:

```
AER_HTTP_USER_AGENT=Ageiantic Research you@example.com
```

Use a real name and a contact address you actually monitor. The SEC requires a descriptive
User-Agent identifying the operator and blocks generic ones — which is why this is the only
setting with no default. A shared default would get everyone rate-limited together.

Leave everything else alone for now. API keys come in section 7.

```powershell
uv python install 3.12
uv sync --all-groups
```

**The first sync downloads and builds the dependencies** and takes a few minutes.

**Wrong:** `uv : The term 'uv' is not recognized`. That is step **0.3a**, not a problem
with this project — `uv` is either not installed or not on this window's `PATH`. Go back
and finish 0.3 before continuing.

**Wrong:** `No `pyproject.toml` found`. You are in the wrong directory. `Get-Location`
should end in `AgeianticEquityResearchPlatform`.

```powershell
uv run aer version
```

**Expect:** a version and a git SHA, e.g. `0.1.0 (3174311)`.

**Wrong:** a `ConfigError` naming `AER_HTTP_USER_AGENT`. That means `.env` is missing or
the value is still blank — and note that the error tells you exactly which variable to set.
That is the intended behaviour, not a failure.

```powershell
uv run alembic upgrade head
```

**Expect:** a series of `Running upgrade` lines ending at **`0006 -> 0007`**.

You will also see this, and it is **not** an error:

```
WARNI [aer.config] AER_SECRET_KEY is not set; generating an ephemeral one for this
process. CSRF tokens will stop verifying when the application restarts.
```

`.env.example` ships that value blank on purpose — a shared default signing key would be
no key at all. Migrations do not sign anything, so it is harmless here. Set it before
section 5, or every restart of the web server will invalidate any form you had open:

```powershell
uv run python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Paste the output into `AER_SECRET_KEY=` in `.env`.

### 2a. When the migration cannot reach the database

A long traceback ending in:

```
ConnectionRefusedError: [WinError 1225] The remote computer refused the network connection
```

Ignore everything above that line — it is the call stack from Alembic down through
SQLAlchemy to the socket, and none of it is the problem. "Connection refused" means the
address was reachable but **nothing is listening on port 5432**. PostgreSQL is not running.

**1. Are the containers up?**

```powershell
docker compose ps
```

**Expect:** `aer-postgres` and `aer-redis`, both `Up ... (healthy)`.

If the list is empty, you never completed section 1.2 — most likely you hit the Docker
engine error first, fixed it, and did not go back:

```powershell
docker compose up -d
```

**2. Did Docker Desktop restart since you last started them?** The containers are set to
`restart: unless-stopped`, so they come back on their own — but only once the engine is
running, and only if you had started them at least once. `docker compose up -d` is safe to
run again either way; it does nothing if they are already up.

**3. Are they still starting?** `(health: starting)` means wait 15 seconds. PostgreSQL
accepts connections a few seconds after the container reports `Started`.

**4. Is it listening where the application is looking?**

```powershell
docker compose port postgres 5432
```

**Expect:** `127.0.0.1:5432`.

If it prints a different port — because you changed `AER_POSTGRES_PORT` to dodge a
conflict — then `AER_DATABASE_URL` in `.env` must use that same port. Those two settings
are separate and have to agree.

**5. Prove it independently of this application:**

```powershell
docker compose exec postgres pg_isready -U aer -d aer
```

**Expect:** `accepting connections`. If that works and `alembic` still refuses, the problem
is the URL in `.env` rather than the database.

**A different error, worth telling apart:** if you get
`password authentication failed for user "aer"`, PostgreSQL *is* running and the credential
is wrong. That is the trap described in section 1.1 — the password was baked into the data
volume the first time the container started, and changing `.env` afterwards does not change
it. Either restore the original password in `.env`, or wipe the volume with
`docker compose down -v` and start again.

### 2b. A note on OneDrive

If your checkout is under `OneDrive\Desktop\...`, it works, but be aware of two things.

OneDrive syncs everything you generate. Once you have run a report, `var/artefacts/`
contains every document the platform fetched, and all of it will be uploaded to Microsoft's
cloud. That is a decision worth making deliberately rather than by accident.

OneDrive also locks files while it uploads them, which can surface as intermittent
permission errors during a test run that writes many small files quickly. If you see
`PermissionError` or `[WinError 32] The process cannot access the file`, that is almost
certainly OneDrive rather than this project.

Both are avoided by keeping the repository outside the synced folder — `C:\dev\` or
`%USERPROFILE%\code\` — which I would recommend, though nothing here requires it.

```powershell
uv run aer seed-user --email you@example.com
```

**Expect:** confirmation that the user was created. The application has no login; this is
the single local user everything belongs to.

```powershell
uv run aer seed-user --email you@example.com
```

**Expect:** it says the user already exists and changes nothing. Running it twice must be
safe.

### Secrets do not leak into the configuration dump

```powershell
uv run just config
```

or, without `just`:

```powershell
uv run python -c "from aer.config import load_settings; print(load_settings().model_dump_json(indent=2))"
```

**Expect:** every key field renders as `**********`.

**Wrong:** any actual key value visible. Put a fake key in `AER_ANTHROPIC_API_KEY`
temporarily and re-run if you want to see the masking work rather than assume it.

---

## 3. The static gates and the test suite

### 3.0 Confirm you are running the code you think you are

Do this first, every time, before reporting a failure. A test failing because your checkout
is three commits behind wastes everyone's time and looks exactly like a real defect.

```powershell
git status -sb
```

**Expect:** `## claude/equity-research-platform-uf2mql...origin/claude/equity-research-platform-uf2mql`
with no `[behind N]`.

**Wrong:** `## main...`, or any other branch. The work lives on
`claude/equity-research-platform-uf2mql`; `git pull` on a different branch fetches a
different branch's commits and reports success while changing nothing you care about.

```powershell
git fetch origin
git status -sb
```

If it now says `[behind 3]` or similar:

```powershell
git checkout claude/equity-research-platform-uf2mql
git pull origin claude/equity-research-platform-uf2mql
```

**Wrong:** `There is no tracking information for the current branch`. A bare `git pull`
does nothing useful in that state and says so in a message that is easy to scroll past.
Naming the remote and branch explicitly, as above, works regardless.

Then check what you actually have:

```powershell
git log --oneline -1
```

Compare it against the tip of the branch on GitHub. If they differ, the pull did not take —
usually because of local edits blocking the merge, which `git status` will list.

### 3.1 The gates

```powershell
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest --ignore=tests/e2e
uv run pytest tests/e2e
```

**Expect:** clean, clean, `Success: no issues found in 121 source files`, then
**1228 passed** (one of which skips on Windows — see below) and **27 passed**.

One test is expected to **skip on Windows** and run on Linux and macOS:
`test_case_differences_are_distinct_on_a_case_sensitive_filesystem`. It simulates a
case-sensitive filesystem by patching `os.path.normcase`, which cannot work on Windows
because `pathlib` compares paths case-insensitively in its own right there. The behaviour
that matters on Windows — two directories differing only in case being *the same*
directory — is a separate test that does run. A third runs everywhere and asserts the
helper agrees with whatever the platform actually does.

**Wrong:** any failure — but check 3.0 first. Four defects have been found this way: two unportable
tests, one where concurrent artefact writes collided on Windows rather than deduplicating,
and one where the background worker could not start at all on any platform. All are fixed,
so a failure now is either a checkout that is behind or something new. The test count is the quickest tell:
1210 means you are several commits behind, 1228 means you are current.

Run the two pytest commands **separately**, not as one `pytest`. Playwright's synchronous
API keeps an event loop running on the main thread for the life of its session, so any
async fixture that follows a browser test in the same process fails with
`Runner.run() cannot be called from a running event loop`. `just test-all` does the split
for you.

The e2e run needs Chromium. If Playwright has not downloaded one:

```powershell
uv run playwright install chromium
```

---

## 4. The tests actually test something

A suite that passes is worth nothing if it would also pass with the code broken. Break
each of these, watch it fail, then undo it with `git checkout <file>`.

| Break this | Where | Expect |
|---|---|---|
| Change `if self._budget is not None` to `if False and ...` | `src/aer/workflow/engine.py` | 4 failures in `TestTheBudgetGuard` |
| Add `import anthropic` at the top of `src/aer/runtime.py` | — | 2 failures in `TestTheImportBoundary` |
| Filter the section loop to the two known keys in `render_markdown` | `src/aer/render/markdown.py` | 5 failures in `TestAThirdSection` |
| Change `/reports/` back to `reports/` | `.gitignore` | 1 failure in `TestNothingUnderSrcIsIgnored` |
| Wrap the sleep in `contextlib.suppress(asyncio.CancelledError)` | `src/aer/api/sse.py` | 1 failure in `test_a_disconnected_reader_ends_the_stream` |

**Wrong:** any of these still passing. That means the guard it protects is not actually
being checked.

---

## 5. The web application, by eye

```powershell
uv run aer serve
```

Open <http://127.0.0.1:8000>.

- [ ] The landing page renders, and the **"Not investment advice"** badge is beside the
      product name at the top — not buried in the footer.
- [ ] `/healthz` returns 200.
- [ ] `/readyz` returns 200 with both `postgres` and `redis` reported.
- [ ] Stop Redis (`docker compose stop redis`), reload `/readyz`: **503**, and the body
      names Redis specifically rather than saying "something is wrong". Start it again.
- [ ] Stop Postgres, reload `/`: the page still renders and tells you the database is
      unreachable and how to start it. **Wrong:** a blank 500. Start it again.
- [ ] `/docs` renders the API documentation.
- [ ] Set `AER_APP_ENV=production` in `.env`, restart, reload `/docs`: **404**. Put it
      back to `development`.

### Every page carries the disclaimer

Visit each of `/`, `/requests`, `/requests/new`, and any request detail page. The footer
disclaimer must be on all of them. It lives in the page shell precisely so a page cannot
ship without it.

### The form works without JavaScript

Disable JavaScript in your browser (DevTools → Settings → Debugger → Disable JavaScript),
then submit `/requests/new` with a deliberately invalid value — an as-of date in the
future.

- [ ] The page comes back with an inline error naming the field.
- [ ] **Everything else you typed is still there.** Losing a page of carefully written
      focus questions to a validation error is a real failure, not a cosmetic one.
- [ ] Correct it and submit: you land on the request's detail page.

Re-enable JavaScript and repeat. The behaviour should be identical apart from the page not
reloading.

---

## 6. Request validation

Create requests through `/requests/new` and confirm each of these is **refused with an
explanation**, not silently accepted:

- [ ] An as-of date in the future.
- [ ] A fund by ticker: `SPY` on `NYSE`. The exchange is supported, so this must be
      refused on the *fund* rule and say so. A fund has no revenue and no margins; the
      whole analysis is a category error.
- [ ] A fund by name: company name `iShares Core MSCI World UCITS ETF`, ticker `IWDA`,
      exchange `LSE`.
- [ ] An investment trust: company name `Scottish Mortgage Investment Trust plc`, ticker
      `SMT`, exchange `LSE`.
- [ ] An OTC venue — pick `AQSE` in the exchange dropdown if it is offered, or post
      `"exchange": "OTCQX"` to `/api/requests`. The error should say the venue is OTC,
      not merely "unsupported".
- [ ] A malformed ISIN — change one character of `US5949181045` so the check digit fails.

In each case check the message names **which** rule refused it. "Not supported" tells you
nothing you can act on.

And confirm these are **normalised** rather than rejected:

- [ ] `msft` becomes `MSFT`, `nasdaq` becomes `NASDAQ`.
- [ ] A URL in "excluded sources" becomes a bare domain.
- [ ] A portfolio weight of `2.5%` round-trips as exactly `0.025` — check the detail page.
      **Wrong:** `0.024999999`. A weight that moves in the third decimal place because it
      passed through a float is a number you cannot reconcile.

---

## 7. A real research run

**This is the highest-value check in the document, and the only one that spends money.**

Two things happen here for the first time outside a test: a real Anthropic call, and the
SEC parsers meeting real EDGAR data. The fixtures in this repository were *constructed to
the documented API shapes*, not recorded from EDGAR — the sandbox this was built in cannot
reach `sec.gov`. So this run is the first evidence that the parsers handle the real thing.

### Set up

Put a real key in `.env`:

```
AER_ANTHROPIC_API_KEY=sk-ant-...
```

Confirm `AER_HTTP_USER_AGENT` is a real name and contact. EDGAR blocks generic ones.

You need **both** processes. Two terminals:

```powershell
uv run aer serve
```

```powershell
uv run arq aer.worker.WorkerSettings
```

**Expect** the worker to log `worker.started`. Without it the run is queued and nothing
happens — which is itself worth seeing once, so you recognise the symptom.

**Neither process reloads code.** After a `git pull` or any edit, stop both with `Ctrl+C` and
start them again. A worker running last week's code will fail a run in a way that looks like
a bug in this week's, and the log line that would tell you otherwise does not exist. Run
`uv run alembic upgrade head` at the same time if migrations changed — the landing page and
`/readyz` both say so when the schema is behind, but only after you have restarted.

### Run it

1. Create a request for **Microsoft Corporation / MSFT / NASDAQ**, as-of date
   **2022-06-30**, point-in-time **on**, max spend **£2.50**.
2. On the request page, click **Start the run**.

- [ ] You land on the run console. It shows the steps as they complete and the spend
      rising.
- [ ] Within a minute or so it stops at **Waiting for you**.

**Wrong:** the console sits at `QUEUED` and never moves, with no worker log lines either.
That means the worker is not running or cannot reach Redis.

If the worker *is* logging — `run_research` picked up, an Anthropic request returning
`200 OK` — but the console still says `QUEUED`, that used to be a defect rather than your
setup: the worker held one transaction for the whole run and published nothing until it
reached a gate. It now commits after each step, so the status should move to `RUNNING` within
a second of the run starting. If it does not, the two processes are pointed at different
databases; check `AER_DATABASE_URL` in both terminals.

### Gate 1

Click **Review the plan**.

- [ ] The plan summary describes what the run intends to do.
- [ ] The sources table names `sec_edgar` at tier `T1_REGULATORY`.
- [ ] **The plan contains no financial figures.** The planner is instructed never to state
      one, because every number in the report must come from deterministic code. If you
      see "revenue of approximately $198bn" in the plan summary, that is a real finding —
      tell me.
- [ ] Known risks are listed.
- [ ] The cost shown is what the planning call actually cost, in pence.

Click **Approve and continue**. You return to the console and the run resumes.

### Gate 2

- [ ] The run reaches **Waiting for you** again. Click **Review the draft**.
- [ ] The preview is the whole document, with a header, sections, footnote markers and a
      sources table.
- [ ] Approve it.

### The report

- [ ] The console shows a **View the report** button. Click it.
- [ ] The badge says **Approved and frozen**.
- [ ] Every figure in the report carries a footnote marker.
- [ ] Each footnote resolves to either a formula and a code version, or a URL, a retrieval
      date and a source tier.
- [ ] The word **"Unresolved citation"** does not appear anywhere. If it does, a footnote
      is pointing at something that no longer exists — tell me.
- [ ] The disclaimer is at the top and the bottom.
- [ ] Click **Download the archived Markdown**. It downloads.

### Sanity-check the number

For MSFT as at 2022-06-30 the report should show revenue compounding at roughly **16–17%**
between the earliest and latest filed periods available at that date.

**Wrong, and important:** a figure computed from revenue of **142,000,000,000** for FY2020.
That is Microsoft's *restated* FY2020 figure, filed in 2022 — after the as-of date. The
admissible figure is **143,015,000,000**, filed in 2020. If the restated number appears,
point-in-time enforcement has failed and the report contains look-ahead bias.

---

## 8. Verify the evidence yourself

The claim this platform makes is that every number traces to a hashed artefact. Check it
by hand rather than taking the report's word for it.

### The archived bytes are the bytes

Take the twelve-character digest from the report's **Sources** table, then:

```powershell
docker compose exec postgres psql -U aer -d aer -c "SELECT sha256, size_bytes, storage_key FROM artefacts WHERE sha256 LIKE '<paste>%';"
```

The `storage_key` column is the path under `AER_ARTEFACT_ROOT`: the first two characters of
the digest, the next two, then the digest in full — `4e/ec/4eec429d…`. Hash the file:

```powershell
Get-FileHash -Algorithm SHA256 var\artefacts\4e\ec\4eec429d19b627e5...
```

- [ ] The hash equals the filename and equals the database row.

**Wrong:** any mismatch. That means stored evidence has been altered or corrupted.

### The report you downloaded is the report that was approved

```powershell
Get-FileHash -Algorithm SHA256 research-2022-06-30-xxxxxxxx.md
```

```powershell
docker compose exec postgres psql -U aer -d aer -c "SELECT content_hash, immutable, approved_at FROM reports;"
```

The download also carries an `X-Artefact-SHA256` response header (visible in DevTools →
Network) which must match the artefact's digest.

- [ ] `immutable` is `true` and `approved_at` is set.

### The calculation records how it was made

```powershell
docker compose exec postgres psql -U aer -d aer -c "SELECT function_ref, formula, output_value, output_unit, code_version FROM calculations;"
```

- [ ] The formula is there, and `code_version` is the git SHA the run used.

Then fetch `/api/calculations/{id}` in your browser and confirm the lineage resolves down
to the financial facts.

### The costs add up

```powershell
docker compose exec postgres psql -U aer -d aer -c "SELECT category, model, units, amount_usd, amount_gbp, fx_rate FROM costs;"
```

- [ ] There is a separate row per category, each with the FX rate on it.
- [ ] The GBP total matches the spend shown on the console.
- [ ] The model is the one your `AER_MODEL_ROUTES` sends `planner` to.

### The point-in-time filter did its job

```powershell
docker compose exec postgres psql -U aer -d aer -c "SELECT concept, period_end, value, filed_date FROM financial_facts WHERE concept = 'revenue' ORDER BY period_end;"
```

- [ ] **No row has a `filed_date` after 2022-06-30.**

---

## 9. The controls, deliberately provoked

These prove the guards fire. None of them spends money.

### The budget cap stops a run before it spends

Create a request with **max spend £0.01** — below what the planner step is projected to
cost. Start it.

- [ ] The console shows **Stopped on budget**.
- [ ] `SELECT * FROM costs WHERE job_id = '...'` returns **nothing**. The cap must stop the
      run *before* the call, not after paying for it.

### An approval cannot be transferred to a different plan

On a run waiting at gate 1, open DevTools, find the hidden `payload_hash` input, change one
character, and submit.

- [ ] The run does not proceed. It stays waiting.

**Wrong:** the run continuing. That would mean the gate accepts an approval of content
nobody was shown.

### A gate cannot be approved twice, or out of order

With a run waiting at gate 1:

```powershell
curl.exe -X POST "http://127.0.0.1:8000/api/runs/<job-id>/gates/FINAL/decide" -H "Content-Type: application/json" -d "{\"decision\":\"APPROVED\",\"payload_hash\":\"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\"}"
```

- [ ] 422, with a message saying the FINAL gate cannot be decided while PLAN has not been
      reached.

Approve gate 1 properly, then approve it again through the API.

- [ ] 422, saying it was already approved.

### CSRF

```powershell
curl.exe -X POST "http://127.0.0.1:8000/runs/<job-id>/gates/PLAN" -d "decision=APPROVED&payload_hash=..."
```

- [ ] 403, and no approval row is created. This matters more than it looks: the app has no
      authentication and listens on loopback, so any page in any browser tab could
      otherwise commission spending on your behalf.

### A killed worker resumes rather than repeating

Start a run, approve gate 1, and **kill the worker with Ctrl-C while it is acquiring**.
Restart it and re-approve nothing — the run resumes on its own next enqueue, or start a
second run of the same request to nudge it.

```powershell
docker compose exec postgres psql -U aer -d aer -c "SELECT step_key, attempt, status FROM job_steps ORDER BY sequence;"
```

- [ ] All eight steps present, all `SUCCEEDED`, and **exactly eight rows** — a resumed run
      must not create a second row for a step it already did.
- [ ] `plan` and `acquire` show `attempt = 0`. Those are the expensive ones: the model call
      and the EDGAR fetch. A resumed run returns their stored output without executing them.

`gate_plan` and `gate_final` will show `attempt = 1` or more, and that is correct — a gate
is entered, pauses, and is entered again after you approve. Being re-entered is what
"resuming" means. It costs nothing.

```powershell
docker compose exec postgres psql -U aer -d aer -c "SELECT agent_role, model, input_tokens, output_tokens FROM agent_runs;"
```

- [ ] Exactly **one** planner row. The planner must not be paid for twice.

### Secrets never reach the log

With `AER_LOG_JSON=true`, run anything that logs and search the output for your API key.

- [ ] It does not appear. Redaction is by field name *and* by value shape, so even a key
      pasted into a URL is masked.

---

## 10. Sections are rows, not code

This is the property Phase 4's user-authored sections depend on. Prove it on your own
machine with an `INSERT` and no code change.

```powershell
docker compose exec postgres psql -U aer -d aer
```

```sql
INSERT INTO section_definitions
  (key, version, origin, title, position, required, output_contract,
   evidence_policy, token_budget, allowed_tools, applicability)
VALUES
  ('competitive_position', 1, 'builtin', 'Competitive Position', 150, true,
   '{"type":"object","title":"Competitive Position","required":["commentary"],
     "properties":{"commentary":{"type":"string","title":"Commentary"},
                   "observations":{"type":"array","title":"Observations",
                                   "items":{"type":"string"}}}}'::json,
   '{"min_sources":1,"requires_primary":true}'::jsonb,
   2000, '{}', '{}'::jsonb);
```

Start a **new** run and take it through both gates.

- [ ] The report has three sections.
- [ ] **Competitive Position** sits between Executive Summary and Historical Financial
      Analysis — position 150, between 100 and 200.
- [ ] Its sub-headings are **Commentary** and **Observations**, which came from the JSON
      Schema you just wrote. No template exists for this section.
- [ ] Footnote numbering is still continuous from 1 with no gaps or duplicates.
- [ ] You changed no Python.

Confirm the existing report is unaffected — a run pins the sections it started with:

- [ ] Re-open the earlier report. It still has two sections.

Clean up if you like:

```sql
DELETE FROM section_definitions WHERE key = 'competitive_position';
```

(If a report has already been rendered against it, the delete is refused. That is correct:
a definition a report was built from must not vanish underneath it.)

### Declared field order survives

```sql
SELECT output_contract::text FROM section_definitions WHERE key = 'executive_summary';
```

- [ ] The properties read `thesis`, then `key_points`, then `key_risks` — the order they
      were declared in.

**Wrong:** `thesis, key_risks, key_points`. That is keys sorted by length, which is what
JSONB does, and it means the column type has regressed to `jsonb`.

---

## 11. The console under real conditions

- [ ] Start a run and watch the console. Steps update **without you reloading**.
- [ ] Disable JavaScript and reload while a run is in progress: the page still shows the
      current state and refreshes itself every 5 seconds.
- [ ] Navigate away from the console mid-run, then check the worker and server logs. The
      event stream should stop. **Wrong:** the server continuing to poll the database for a
      page nobody is looking at.
- [ ] Open the console for a finished run: no auto-refresh, and a **View the report**
      button.

### 11a. A long step must not look like a dead one

This is the case the console is built for, and the only one where "it looks fine" is not
enough. A step that calls a model can run for five minutes and change nothing in the
database, so every static thing on the page stays exactly where it is.

- [ ] While the first step is running, the whole workflow is listed, most of it `QUEUED`.
      **Wrong:** one line, and no sense of how much is left.
- [ ] The running step has a pulsing marker and a clock. **Watch the clock for ten
      seconds: it must advance.** That is the signal separating a model mid-thought from a
      browser tab that lost its connection.
- [ ] The clock counts from when the *server* recorded the step as starting, not from when
      you opened the page. Reload mid-step: the number should barely change.
- [ ] Below the steps, "Server last checked at …" appears within about fifteen seconds and
      then keeps updating. That is the event-stream heartbeat. It says the **web process**
      is alive; it deliberately does not claim the worker is.
- [ ] Now prove the difference. Stop the worker (`Ctrl-C`) mid-run and leave the console
      open. The clock keeps ticking and the heartbeat keeps arriving — correctly, because
      neither can see the worker — and nothing else changes. That standstill, with the
      elapsed time climbing past ten minutes, is what a dead worker looks like, and the
      banner tells you where to look for it.
- [ ] Break a step deliberately (an invalid `AER_ANTHROPIC_API_KEY` is easiest) and let it
      fail. The console shows the **sentence** — "The Anthropic API call failed …" — with
      the error code beneath it. **Wrong:** the whole error dictionary printed as one line,
      with the message buried in the middle of it.

**Where the worker's log is.** There is no log file and no worker container: `arq` writes
to stdout, so the log is the terminal you ran `just worker` in. To keep a copy:
`just worker 2>&1 | tee var/worker.log`, or on PowerShell
`just worker 2>&1 | Tee-Object -FilePath var\worker.log`. `var/` is git-ignored.

---

## 12. Things that should be impossible

- [ ] Open a run belonging to nobody: `/runs/00000000-0000-0000-0000-000000000000` → a
      page saying it is not available, **404**, and no stack trace.
- [ ] `/api/runs/<random-uuid>` → 404 with a machine-readable `code`, not a 500.
- [ ] Force an error and check the response body contains a request id and **no internal
      message and no traceback** — while the full traceback *is* in the server log.
- [ ] Every response carries an `X-Request-ID`, and that same id appears in the log lines
      for that request.

---

## 12a. After you pull: run the migrations

**Do this first, every time you pull.** New code frequently needs new tables, and the
symptom of forgetting is not obvious: the application starts cleanly, most pages work, and
one page returns a 500 whose only clue is in a stack trace.

```powershell
uv run alembic upgrade head
uv run alembic current      # should print the newest revision followed by (head)
```

The platform now tells you rather than letting you find out. All three of these say the
same thing, and it is worth knowing where each of them is:

- [ ] **At start-up.** `uv run aer serve` logs a `schema.out_of_date` warning in the
      console naming the missing tables. It is a warning, not a refusal — an application
      that would not start because of a pending migration would take away the landing page
      that could have explained it.
- [ ] **On the landing page.** A "Not ready yet" banner naming the missing tables and the
      command to run.
- [ ] **On `/readyz`.** `schema` appears alongside `database` and `redis`, and a stale
      schema gives a **503** with `"error": "SchemaOutOfDateError"` and a detail naming
      what is missing.

To see all three deliberately, downgrade one step and look:

```powershell
uv run alembic downgrade -1
uv run aer serve            # watch the console, then open / and /readyz
uv run alembic upgrade head
```

- [ ] With the schema behind, `/readyz` is 503 and `database` still reports **ok**.
      Connectivity and schema are different problems with different fixes; reporting the
      second as the first would send you to restart a container that was working perfectly.

---

## 13. Correcting or removing a draft request

A request you have not run yet is a note to itself, and mistyping a ticker should cost you
nothing. The moment a run exists it stops being a note and becomes the record of what was
researched — so editing it would not correct anything, it would falsify it. That line is
the whole feature, and this section checks the platform actually holds it.

### While it is still a draft

- [ ] Create a request. On its detail page there are **Edit this request** and **Delete**
      buttons.
- [ ] Click **Edit this request**. Every field is **prefilled with what you saved** — not
      blank, and not defaults. Check the portfolio weight in particular: if you entered
      `2.5%` the box must read `2.5`, not `0.025`. Rendering the stored fraction into a box
      labelled "%" would divide your weight by a hundred every time you pressed save.
- [ ] Change the company name and save. You land back on the detail page showing the new
      name.
- [ ] Edit again and submit something invalid — an as-of date in the future, say. The page
      is refused, says **"This request was not saved"**, and **everything else you typed is
      still in the boxes**.
- [ ] Confirm the same rules apply as at creation: try `TSX` as the exchange, or a cost
      above your per-run budget. Both must be refused. A rule that only creation enforces
      is a rule anyone can get around by creating a valid request and then editing it.
- [ ] Delete a draft you do not want. You are asked to confirm, then returned to
      `/requests`, and it is gone from the list.

### Once a run has left something behind

What freezes a request is not that a run started — it is what the run produced. A run
cancelled before it fetched or spent anything leaves nothing an edit could falsify, so the
request stays editable. Check both halves.

- [ ] Start a run on a request, then go back to `/requests/<id>` while it is still queued
      or running.
- [ ] The **Edit** and **Delete** buttons are **gone**, replaced by a sentence saying to
      wait for the run or cancel it. **Wrong:** greyed-out buttons — a disabled button
      invites you to hunt for the condition that re-enables it.
- [ ] Navigate directly to `/requests/<id>/edit`. You get a **409** page with the reason on
      it, not a blank form and not a bare error code.
- [ ] Try the API: `curl -i -X DELETE http://127.0.0.1:8000/api/requests/<id>`. **409**,
      with `"code": "conflict"`. Not 422 — the body was never the problem, and resubmitting
      it would not help.
- [ ] Confirm the request is still there afterwards. `research_requests` cascades to jobs,
      plans, sources and reports, so a delete allowed here would take all of it.

### Spend does not block deletion, and must not be lost by it

The planner runs before you can press stop, so almost every cancelled request has cost
something. That used to block deletion — which made the button theoretical. The real
problem was that `costs` cascaded away with the request, so deleting one erased its spend
history. Migration 0009 fixed the cascade; the refusal was then unnecessary.

- [ ] Note the month's spend before you start:

```sql
SELECT sum(amount_gbp) FROM costs;
```

- [ ] Take a request that reached the plan gate, cancel it, let the worker stop it, then
      delete it from `/requests/<id>`. It should delete.
- [ ] Run the sum again. **It must be unchanged.** A total that drops when you delete a
      request is a monthly cap you can get under by tidying up, which is not a cap.
- [ ] Check the orphaned rows are still explicable:

```sql
SELECT payload->>'ticker', payload->>'spend_gbp'
FROM audit_events WHERE event_type = 'request.deleted' ORDER BY id DESC LIMIT 3;

SELECT count(*) FROM costs WHERE job_id IS NULL;
```

      The cost rows lose their `job_id` — that is the `SET NULL` working — and the
      `request.deleted` audit entry records what they were spent on. The audit log is
      designed to outlive what it describes, which is why the attribution lives there.

### The audit trail

```sql
SELECT event_type, payload FROM audit_events
WHERE event_type IN ('request.edited', 'request.deleted')
ORDER BY id DESC LIMIT 5;
```

- [ ] The edit entry carries a `changes` object with **before and after** for each field
      you changed, and nothing for the fields you did not.
- [ ] Money in that payload is a **string** — `"2.00"`, not `2.0`. JSON has no decimal
      type, and a cost ceiling that comes back as `1.4999999999999998` is worse than
      useless.
- [ ] The delete entry is still there after the row is gone, and names the ticker.
      `audit_events.request_id` is deliberately not a foreign key, so the record outlives
      the thing it describes.

---

## 14. Cancelling a run

Cancelling is a **request**, not an act. The worker is inside a step — an HTTP fetch, a
model call — and neither can be interrupted from another process without abandoning work
already paid for. So the button records the request and the run stops at its next step
boundary. That is the finest granularity that can be reported honestly, and the console
says so rather than pretending the run stopped the instant you clicked.

- [ ] Start a run. While it is going, the console shows a **Cancel this run** button and an
      optional reason box.
- [ ] Type a reason — "wrong as-of date" — and press it. You return to the console, which
      now shows a **Stopping** panel with your reason and the time you asked.
- [ ] The cancel button is **gone**. Asking twice is idempotent, but a button that stays
      offered invites a second click that does nothing visible.
- [ ] Watch the worker log. You should see `workflow.cancelled` with a `before_step`
      naming the step it stopped *before* — and the step that was already running should
      have logged `workflow.step_completed` first. **Wrong:** a step abandoned halfway.
- [ ] Reload the console. The status reads `CANCELLED` and there is no auto-refresh.
- [ ] Press cancel on a run that has already finished — or `curl -i -X POST
      http://127.0.0.1:8000/api/runs/<id>/cancel` against a succeeded run. **409**. Not
      because the write would fail, but because recording it would put something in the
      audit trail that did not happen.

### Cancelling must not be a dead end

This is the check that matters most, because getting it wrong leaves you with a request you
can neither run, fix nor throw away. That is exactly what the first version did.

- [ ] Go back to `/requests/<id>` after the run has reached `CANCELLED`.
- [ ] There is a **Start a new run** button, and a link to the cancelled run beside it.
      **Wrong:** only "Open the run", with no way forward — that is the dead end.
- [ ] Press it. You land on a **different** console URL, and the new run is `QUEUED`, not
      `CANCELLED`. A resurrected job would carry the old cancellation and stop again on its
      first step.
- [ ] The cancelled run is still openable at its own URL. Superseded, not erased.
- [ ] Cancel a run *before* the planner has spent anything — press stop while it is still
      `QUEUED` and let the worker pass over it. The request should now be **editable and
      deletable again**: a run that gathered nothing, cited nothing and spent nothing leaves
      nothing an edit could falsify.

```sql
SELECT j.status, c.requested_at, j.finished_at, c.reason
FROM job_cancellations c JOIN jobs j ON j.id = c.job_id
ORDER BY c.requested_at DESC LIMIT 5;
```

- [ ] `requested_at` is **earlier** than `finished_at`. The gap is how long the step in
      flight took to finish, and both times are kept precisely because they differ.

```sql
SELECT payload FROM audit_events
WHERE event_type = 'run.cancellation_requested' ORDER BY id DESC LIMIT 1;
```

- [ ] The payload records `status_when_requested`. Reconstructing what the run was doing
      from timestamps alone is guesswork.

### Why a separate table, if you are wondering

The worker sets `jobs.status = RUNNING` and commits only when the whole run ends, so it
holds that row's lock for the run's entire lifetime. A cancel that wrote to `jobs` would
wait for exactly as long as cancelling remained useful. You can prove this to yourself:
open `psql`, `BEGIN; UPDATE jobs SET status = 'CANCELLED' WHERE id = '<a running job>';`
and watch it hang. That measurement is why `job_cancellations` exists.

---

## 15. The report, and walking a figure back to bytes

Approve a run at both gates so it produces a frozen report, then open
`/reports/{report_id}`.

### The three notations are one document

1. Follow **Open the document preview**. That page is the report's own HTML, with no site
   navigation and no script of its own.
2. Download the Markdown, the HTML and the PDF. Each response carries an
   `X-Artefact-SHA256` header; compute the digest of the bytes you received and check it
   matches. It is the archived artefact being served, not a re-render.
3. Open the PDF. The bookmark tree should carry one entry per section the run produced —
   including any custom section you enabled — and the cover, running headers and page
   numbers should be present. Try to edit it: it is encrypted with permissions denying
   modification, with the content hash as the owner password.

### Every marker is a door

In the preview, hover a superscript footnote marker. The note's text appears as a tooltip
— that is a `title` attribute, so turn JavaScript off and confirm it still does.

4. Click the marker. You land on the note.
5. Click the note's **evidence** link. For a source you should see the document's **full**
   SHA-256, its tier, its licence note, and every claim in the run checked against it —
   each excerpt printed verbatim, with the verifier's verdict and match ratio beside it. A
   claim whose excerpt did not verify appears here saying so; that is the most important
   thing this page can show.
6. Find a marker on a **calculated** figure. It leads to the calculation walk: the formula,
   the code version, and the inputs to their leaves. Every leaf should be a fact or an
   assumption — never another calculation. An assumption states its justification in place;
   a fact links on to the document it was reported in, where its artefact digest is.

If any marker leads nowhere, that is the defect this whole surface exists to prevent.

### The vault, if you have one configured

Set `AER_OBSIDIAN_VAULT_ROOT` (and `AER_OBSIDIAN_PERSONAL_ROOT`, to a directory that is
**not** inside it) and export the report from the report page.

7. Open the vault in Obsidian. Every wiki-link should resolve — no broken-link styling
   anywhere, including in the industry, catalyst and competitor notes.
8. Write something below the `<!-- AER:END-GENERATED -->` marker in a company note, then
   export again. Your text must be exactly as you left it, and the marker must appear once.
9. Export the same report twice and compare the vault's files: they should be byte-identical
   between exports. Only the `obsidian_exports` row differs, because only the act differs.
10. Try to export an **unapproved** report. The page should refuse and say why.

---

## What is not covered here, and why

**Restart resilience of the queue.** arq's own persistence is not something this project
tests; a run interrupted by a machine restart is recovered by re-enqueueing it, not
automatically.

**Retention and safe deletion.** Only the safest slice of it is built: a request can be
deleted while it is a draft with no run (section 13). Anything with evidence, costs or a
report behind it cannot be deleted by any code path in the platform. A real retention
policy — deleting a report *and* its artefacts, provably and with a record — is Phase 6.

**Pausing a run.** Not built, deliberately. A run can be cancelled (section 14) but not
paused and resumed on demand. `docs/archive/PLAN.md` §2.7 lists both; pausing needs a story about
what happens to a half-fetched evidence set, and inventing one now would be guessing.

**Audit-chain verification on a schedule.** `aer.core.hashing.verify_chain` exists and is
tested, but nothing runs it periodically yet. You can call it by hand over the
`audit_events` table if you want to confirm the chain is intact.

**Anything beyond one US filing.** UK filings, prices, macro data, peers, valuation and
the red-team pass are Phase 2 and 3. The slice deliberately does one of everything.

---

## If something fails

Note which check, what you saw, and what the logs said. A failure in section 7 or 8 is the
most interesting kind — those are the paths that have never met real data or a real model,
and they are where I would expect the first genuine defect to be.
