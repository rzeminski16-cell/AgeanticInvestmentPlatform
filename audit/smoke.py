"""The offline proof that the driver works, before any money moves.

    uv run python -m audit.smoke
    uv run python -m audit.smoke --journey

Two scenes on a scratch database the platform's own migrations build:

1. **The fake scene, end to end.** Microsoft through the whole workflow against the suite's
   own fake provider and stub filing client, every gate cleared by the audit's policy — the
   same objects the suite's full-run golden test uses (``tests/workflow_fixtures.py``).
2. **The UK refusal, at the door.** A domestic LSE filer (Tesco) put to the pre-run check
   (ADR 0128) against a register answering exactly as the live one did on 18 September 2026:
   the company resolves, it has filed accounts, and its newest set has no tagged copy. The
   run must be refused before a job exists — no planning call, no acquisition, nothing spent
   — and the refusal must name the *filing* rather than the company.

   It used to drive the same subject with the real EDGAR client and pass when acquisition
   failed on an unknown ticker. Since `acquire` dispatches on the venue, EDGAR is never asked
   about a London listing, so that scene proved a path production cannot take — and it made
   the only real network request in this harness, which now makes none.

With ``--journey``, neither scene: the journey harness's shape half instead (`audit/journey.py`),
every stopped state a run can be left in, rendered by the real handlers and asserted on in
process, on the same scratch database.

Reads ``.env`` for the model key only to satisfy settings validation; the fake provider
answers every call. Writes to ``audit/out/smoke-*``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import tempfile
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

SCRATCH_DB = "aer_audit_smoke"


def _scratch_url() -> str:
    base = (
        os.environ.get("AER_DATABASE_URL")
        or "postgresql+asyncpg://aer:aer_local_dev@127.0.0.1:5432/aer"
    )
    root, _, _ = base.rpartition("/")
    return f"{root}/{SCRATCH_DB}"


async def _recreate(url: str) -> None:
    root, _, _ = url.rpartition("/")
    engine = create_async_engine(f"{root}/postgres", isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{SCRATCH_DB}" WITH (FORCE)'))
            await conn.execute(text(f'CREATE DATABASE "{SCRATCH_DB}"'))
    finally:
        await engine.dispose()


def _prepare(url: str, artefacts: Path) -> None:
    env = {**os.environ, "AER_DATABASE_URL": url, "AER_ARTEFACT_ROOT": str(artefacts)}
    subprocess.run(
        ["uv", "run", "alembic", "upgrade", "head"], check=True, env=env, capture_output=True
    )
    subprocess.run(
        ["uv", "run", "aer", "seed-user", "--email", "audit-smoke@example.invalid"],
        check=True,
        env=env,
        capture_output=True,
    )


async def _scenes(url: str, artefacts: Path) -> dict[str, Any]:
    os.environ["AER_DATABASE_URL"] = url
    os.environ["AER_ARTEFACT_ROOT"] = str(artefacts)
    sys.path.insert(0, str(Path.cwd()))

    from tests.api_fixtures import admitting_registers  # noqa: PLC0415
    from tests.uk_fixtures import RecordedCompaniesHouse  # noqa: PLC0415
    from tests.workflow_fixtures import (  # noqa: PLC0415
        StubSecClient,
        make_provider,
        with_price_feed,
    )

    from aer.runtime import Registers  # noqa: PLC0415
    from audit.driver.run import drive  # noqa: PLC0415
    from audit.driver.session import AuditRuntime  # noqa: PLC0415
    from audit.subjects import subject_for  # noqa: PLC0415

    runtime = await AuditRuntime.open(with_bundle=False)
    runtime.resolved = with_price_feed(runtime.resolved)
    out_root = Path("audit/out")
    results: dict[str, Any] = {}
    try:
        store = __import__("aer.storage.local", fromlist=["LocalArtefactStore"]).LocalArtefactStore(
            runtime.resolved.artefact_root, max_bytes=runtime.resolved.max_artefact_bytes
        )
        fake = {
            "provider": make_provider(),
            "store": store,
            "sec_client": StubSecClient(store),
            "fetcher": None,
            "eodhd_client": None,
        }
        results["fake_scene"] = await drive(
            subject_for("msft1"),
            cap_gbp=Decimal("12.00"),
            executor_kind="inline",
            runtime=runtime,
            services=fake,
            out_root=out_root,
            label="smoke-msft",
            # The check would ask EDGAR about a subject whose whole run is fake, so it is
            # given a register that admits everything — the same arrangement the suite's own
            # application fixtures use, and for the same reason.
            registers=admitting_registers(),
        )

        results["uk_refusal"] = await drive(
            subject_for("tsco"),
            cap_gbp=Decimal("12.00"),
            executor_kind="inline",
            runtime=runtime,
            services=fake,
            max_resumes=0,
            out_root=out_root,
            label="smoke-tsco",
            registers=Registers(
                sec_client=StubSecClient(store),  # type: ignore[arg-type]
                companies_house_client=RecordedCompaniesHouse(),  # type: ignore[arg-type]
            ),
        )
    finally:
        await runtime.close()
    return results


def _journey(url: str, artefacts: Path, only: str | None) -> int:
    """The journey harness's shape half, on the same scratch database. See `audit/journey.py`."""
    os.environ["AER_DATABASE_URL"] = url
    os.environ["AER_ARTEFACT_ROOT"] = str(artefacts)
    # Fifty-odd applications start and stop here, one per row; their request logs would bury
    # the verdicts. A signing key so that each does not announce it minted one of its own —
    # the forms are posted back to the application that rendered them, on a scratch database.
    os.environ.setdefault("AER_LOG_LEVEL", "WARNING")
    os.environ.setdefault(
        "AER_SECRET_KEY", "journey-shape-half-not-a-real-one"
    )  # pragma: allowlist secret
    sys.path.insert(0, str(Path.cwd()))

    from audit.journey import run_journey  # noqa: PLC0415

    verdicts = run_journey(url, out_dir=Path("audit/out/smoke-journey"), only=only)
    wrong = [verdict for verdict in verdicts if not verdict.ok]
    tally = {
        "rows": len(verdicts),
        "green": sum(verdict.measured == "green" for verdict in verdicts),
        "red": sum(verdict.measured == "red" for verdict in verdicts),
        "unconstructed": sum(verdict.measured == "unconstructed" for verdict in verdicts),
        "errors": sum(verdict.measured == "error" for verdict in verdicts),
        "not_as_recorded": [verdict.key for verdict in wrong],
    }
    print(json.dumps(tally, indent=2))
    return 1 if wrong or not verdicts else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--journey",
        action="store_true",
        help="run the journey harness's shape half instead of the two scenes",
    )
    parser.add_argument(
        "--only",
        metavar="REGEX",
        help="with --journey: only the rows whose key matches",
    )
    args = parser.parse_args(argv)
    url = _scratch_url()
    artefacts = Path(tempfile.mkdtemp(prefix="aer-audit-smoke-"))
    asyncio.run(_recreate(url))
    _prepare(url, artefacts)
    if args.journey:
        return _journey(url, artefacts, args.only)
    results = asyncio.run(_scenes(url, artefacts))
    refusal = str(results["uk_refusal"].get("refusal") or "")
    verdict = {
        "fake_scene_status": results["fake_scene"].get("status"),
        "fake_scene_acceptance": results["fake_scene"].get("acceptance_passed"),
        "uk_refusal_status": results["uk_refusal"].get("status"),
        "uk_refusal_job": results["uk_refusal"].get("job_id"),
        "uk_refusal_reason": refusal,
    }
    print(json.dumps(verdict, indent=2))
    ok = (
        verdict["fake_scene_status"] == "SUCCEEDED"
        and verdict["uk_refusal_status"] == "REFUSED"
        # No job, because the refusal is at the door rather than three steps in.
        and not verdict["uk_refusal_job"]
        # And it is about the filing. "This company cannot be researched" would be a
        # different and untrue statement about Tesco.
        and "scanned document" in refusal
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
