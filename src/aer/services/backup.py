"""Taking a copy of everything, and proving the copy is readable.

Gap A10. The integrity sweep added in A9 can tell an operator the artefact store has lost a
document; until this module there was nothing to put it back. Two halves have to be copied
together or neither is worth much: the database holds every claim, calculation and citation,
and the artefact store holds the bytes those citations point *at*. A database restored
beside an empty store is a set of citations into nothing.

**A backup that has never been read is not a backup.** So :func:`verify_backup` is a first
class part of this rather than a nicety: it re-hashes the dump and every archived file
against the manifest, without touching a database, and it is what `aer restore` runs before
it is willing to do anything destructive.

**Credentials never reach a command line.** ``pg_dump`` is given host, port, user and
database as discrete arguments and the password through ``PGPASSWORD`` in its environment.
A URL passed as an argument would put the password in ``ps`` output for every user on the
machine, which is a worse leak than a log line and harder to notice.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import structlog
from sqlalchemy.engine import make_url

from aer.core.hashing import canonical_json, sha256_hex
from aer.errors import AerError
from aer.version import version

__all__ = [
    "ARTEFACT_INDEX_NAME",
    "DATABASE_DUMP_NAME",
    "MANIFEST_NAME",
    "BackupError",
    "BackupManifest",
    "BackupReport",
    "create_backup",
    "read_manifest",
    "restore_backup",
    "verify_backup",
]

_log = structlog.get_logger("aer.services.backup")

DATABASE_DUMP_NAME: Final = "database.dump"
ARTEFACT_INDEX_NAME: Final = "artefacts.txt"
MANIFEST_NAME: Final = "manifest.json"
_ARTEFACT_DIR_NAME: Final = "artefacts"

_READ_CHUNK_BYTES: Final = 1024 * 1024

# Long enough for a real dump on a slow disk, bounded so a hung pg_dump fails the backup
# rather than leaving a cron job running until somebody notices.
_DUMP_TIMEOUT_SECONDS: Final = 3600


class BackupError(AerError):
    """A backup could not be taken, verified or restored."""

    code = "backup_failed"


@dataclass(frozen=True, slots=True)
class BackupManifest:
    """What a backup claims to contain, hashed so the claim can be checked."""

    created_at: str
    aer_version: str
    schema_revision: str
    database_sha256: str
    database_bytes: int
    artefact_count: int
    artefact_bytes: int
    artefacts_sha256: str

    def as_dict(self) -> dict[str, object]:
        return {
            "created_at": self.created_at,
            "aer_version": self.aer_version,
            "schema_revision": self.schema_revision,
            "database_sha256": self.database_sha256,
            "database_bytes": self.database_bytes,
            "artefact_count": self.artefact_count,
            "artefact_bytes": self.artefact_bytes,
            "artefacts_sha256": self.artefacts_sha256,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, object]) -> BackupManifest:
        try:
            return cls(
                created_at=str(raw["created_at"]),
                aer_version=str(raw["aer_version"]),
                schema_revision=str(raw["schema_revision"]),
                database_sha256=str(raw["database_sha256"]),
                database_bytes=int(str(raw["database_bytes"])),
                artefact_count=int(str(raw["artefact_count"])),
                artefact_bytes=int(str(raw["artefact_bytes"])),
                artefacts_sha256=str(raw["artefacts_sha256"]),
            )
        except (KeyError, ValueError) as exc:
            message = f"The backup manifest is not readable: {exc}."
            raise BackupError(message, context={"missing": str(exc)}) from exc


@dataclass(frozen=True, slots=True)
class BackupReport:
    """What re-reading a backup found."""

    checked: int
    manifest: BackupManifest | None = None
    problems: tuple[str, ...] = ()

    @property
    def is_sound(self) -> bool:
        return not self.problems


def create_backup(
    *, database_url: str, artefact_root: Path, destination: Path, schema_revision: str
) -> BackupManifest:
    """Copy the database and the artefact store into ``destination``.

    The destination must not already hold a backup. Writing a second one over the first
    would leave a directory that is neither, and the failure would only surface on the day
    somebody needed to restore it.
    """
    manifest_path = destination / MANIFEST_NAME
    if manifest_path.exists():
        message = (
            f"{destination} already contains a backup taken at "
            f"{read_manifest(destination).created_at}. Refusing to write over it: a "
            "half-overwritten backup is neither the old one nor the new one."
        )
        raise BackupError(message, context={"destination": str(destination)})

    destination.mkdir(parents=True, exist_ok=True)
    dump_path = destination / DATABASE_DUMP_NAME
    _dump_database(database_url, dump_path)

    artefact_index, count, total_bytes = _copy_artefacts(
        artefact_root, destination / _ARTEFACT_DIR_NAME
    )
    index_path = destination / ARTEFACT_INDEX_NAME
    index_path.write_text(artefact_index, encoding="utf-8")

    manifest = BackupManifest(
        created_at=datetime.now(UTC).isoformat(),
        aer_version=version(),
        schema_revision=schema_revision,
        database_sha256=_digest_file(dump_path),
        database_bytes=dump_path.stat().st_size,
        artefact_count=count,
        artefact_bytes=total_bytes,
        artefacts_sha256=sha256_hex(artefact_index),
    )
    manifest_path.write_text(canonical_json(manifest.as_dict()), encoding="utf-8")

    _log.info(
        "backup.created",
        destination=str(destination),
        artefacts=count,
        database_bytes=manifest.database_bytes,
    )
    return manifest


def read_manifest(directory: Path) -> BackupManifest:
    path = directory / MANIFEST_NAME
    if not path.is_file():
        message = (
            f"{directory} does not look like a backup: there is no {MANIFEST_NAME}. "
            "Without it nothing here can be checked before being restored."
        )
        raise BackupError(message, context={"directory": str(directory)})

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        message = f"The backup manifest at {path} is not valid JSON: {exc}."
        raise BackupError(message, context={"directory": str(directory)}) from exc
    if not isinstance(raw, dict):
        message = f"The backup manifest at {path} is not an object."
        raise BackupError(message, context={"directory": str(directory)})
    return BackupManifest.from_dict(raw)


def verify_backup(directory: Path) -> BackupReport:
    """Re-hash everything in a backup and check it against the manifest.

    Reads no database, so it can be run against a backup on a machine that has none — which
    is where a backup usually ends up, and where a restore is usually first attempted.

    Every problem is collected rather than the first one raised: an operator checking a
    backup wants to know whether it is usable at all, and stopping at the first bad file
    turns one pass into as many passes as there are faults.
    """
    manifest = read_manifest(directory)
    problems = _check_database_dump(directory, manifest)

    index_path = directory / ARTEFACT_INDEX_NAME
    if not index_path.is_file():
        problems.append(f"the artefact index {ARTEFACT_INDEX_NAME} is missing")
        return BackupReport(checked=0, manifest=manifest, problems=tuple(problems))

    checked, artefact_problems = _check_artefacts(directory, manifest)
    problems.extend(artefact_problems)

    if checked != manifest.artefact_count and not problems:
        problems.append(
            f"the backup holds {checked:,} artefacts, the manifest says {manifest.artefact_count:,}"
        )

    return BackupReport(checked=checked, manifest=manifest, problems=tuple(problems))


def _check_database_dump(directory: Path, manifest: BackupManifest) -> list[str]:
    """Size before hash: a truncated dump is the common failure and is cheap to spot."""
    dump_path = directory / DATABASE_DUMP_NAME
    if not dump_path.is_file():
        return [f"the database dump {DATABASE_DUMP_NAME} is missing"]

    actual_bytes = dump_path.stat().st_size
    if actual_bytes != manifest.database_bytes:
        return [
            f"the database dump is {actual_bytes:,} bytes, "
            f"the manifest says {manifest.database_bytes:,}"
        ]
    if _digest_file(dump_path) != manifest.database_sha256:
        return ["the database dump does not match the hash in the manifest"]
    return []


def _check_artefacts(directory: Path, manifest: BackupManifest) -> tuple[int, list[str]]:
    """Every listed artefact re-hashed, and anything present that is not listed.

    Both directions matter. A missing file is data loss; a file in the tree that the index
    does not mention means the index and the tree disagree, and an index that cannot be
    trusted makes the "nothing is missing" half of this check worthless.
    """
    index_text = (directory / ARTEFACT_INDEX_NAME).read_text(encoding="utf-8")
    problems: list[str] = []
    if sha256_hex(index_text) != manifest.artefacts_sha256:
        problems.append(
            "the artefact index does not match the hash in the manifest, so the list of "
            "what should be here cannot be trusted either"
        )

    root = directory / _ARTEFACT_DIR_NAME
    listed: set[str] = set()
    checked = 0
    for line in index_text.splitlines():
        if not line.strip():
            continue
        digest, _, relative = line.partition(" ")
        listed.add(relative)
        path = root / relative
        if not path.is_file():
            problems.append(f"artefact {digest} is listed but not present")
            continue
        checked += 1
        if _digest_file(path) != digest:
            problems.append(f"artefact {digest} does not hash to its name")

    if root.is_dir():
        present = {str(path.relative_to(root)) for path in root.rglob("*") if path.is_file()}
        problems.extend(
            f"{extra} is in the backup but not in its index" for extra in sorted(present - listed)
        )

    return checked, problems


def restore_backup(
    *, directory: Path, database_url: str, artefact_root: Path, verify_first: bool = True
) -> BackupReport:
    """Put a backup back. **Destructive**: the target database is dropped and rebuilt.

    ``verify_first`` defaults to on and should stay on. Restoring from a backup nobody has
    checked is how a corrupt dump replaces a working database, and the moment of restore is
    the worst possible time to discover the copy was bad.

    Artefacts are copied in rather than replacing the store wholesale. They are
    content-addressed, so a file already present under a given digest *is* the file in the
    backup, and overwriting it would be work with no effect.
    """
    report = verify_backup(directory) if verify_first else BackupReport(checked=0)
    if verify_first and not report.is_sound:
        message = (
            f"The backup at {directory} does not verify, so it will not be restored: "
            + "; ".join(report.problems[:3])
            + ". Restoring an unverified copy over a working database is how one bad "
            "backup becomes two."
        )
        raise BackupError(message, context={"directory": str(directory)})

    _restore_database(database_url, directory / DATABASE_DUMP_NAME)

    source_root = directory / _ARTEFACT_DIR_NAME
    restored = 0
    if source_root.is_dir():
        for path in sorted(source_root.rglob("*")):
            if not path.is_file():
                continue
            target = artefact_root / path.relative_to(source_root)
            if target.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            restored += 1

    _log.info("backup.restored", directory=str(directory), artefacts_written=restored)
    return report


def _copy_artefacts(source: Path, destination: Path) -> tuple[str, int, int]:
    """Copy the store and return its index, the file count and the total bytes.

    The index is sorted so the same store always produces the same text, and therefore the
    same hash — a manifest whose hash changed between two identical backups would make
    "has this backup changed?" unanswerable.
    """
    lines: list[str] = []
    count = 0
    total = 0

    if source.is_dir():
        for path in sorted(source.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(source)
            # `tmp` holds partial writes: bytes that have no content address yet and that
            # `prune_temp_files` deletes. Copying them would put files in the backup that
            # can never be checked against their name.
            if relative.parts and relative.parts[0] == "tmp":
                continue
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            digest = _digest_file(target)
            lines.append(f"{digest} {relative}")
            count += 1
            total += target.stat().st_size

    return ("\n".join(sorted(lines)) + "\n" if lines else ""), count, total


def _dump_database(database_url: str, destination: Path) -> None:
    url = make_url(database_url)
    command = [
        "pg_dump",
        "--format=custom",
        "--no-owner",
        "--no-privileges",
        "--host",
        str(url.host or "127.0.0.1"),
        "--port",
        str(url.port or 5432),
        "--username",
        str(url.username or ""),
        "--dbname",
        str(url.database or ""),
        "--file",
        str(destination),
    ]
    _run(command, url.password, what="pg_dump")


def _restore_database(database_url: str, dump: Path) -> None:
    if not dump.is_file():
        message = f"There is no database dump at {dump} to restore from."
        raise BackupError(message, context={"dump": str(dump)})

    url = make_url(database_url)
    command = [
        "pg_restore",
        "--clean",
        "--if-exists",
        "--no-owner",
        "--no-privileges",
        "--host",
        str(url.host or "127.0.0.1"),
        "--port",
        str(url.port or 5432),
        "--username",
        str(url.username or ""),
        "--dbname",
        str(url.database or ""),
        str(dump),
    ]
    _run(command, url.password, what="pg_restore")


def _run(command: list[str], password: object, *, what: str) -> None:
    """Run a Postgres tool with the password in its environment, never in its arguments."""
    environment = dict(os.environ)
    if password:
        environment["PGPASSWORD"] = str(password)

    try:
        completed = subprocess.run(  # noqa: S603 -- fixed argv, no shell, no user input
            command,
            capture_output=True,
            text=True,
            timeout=_DUMP_TIMEOUT_SECONDS,
            env=environment,
            check=False,
        )
    except FileNotFoundError as exc:
        message = (
            f"{what} is not on PATH. The backup commands shell out to the PostgreSQL client "
            "tools; install the client package matching your server version."
        )
        raise BackupError(message, context={"tool": what}) from exc
    except subprocess.TimeoutExpired as exc:
        message = f"{what} did not finish within {_DUMP_TIMEOUT_SECONDS}s and was killed."
        raise BackupError(message, context={"tool": what}) from exc

    if completed.returncode != 0:
        # stderr can name the host and database but never the password: it went in through
        # the environment, and Postgres tools do not echo it.
        message = f"{what} failed with exit code {completed.returncode}: {completed.stderr.strip()}"
        raise BackupError(message, context={"tool": what, "returncode": completed.returncode})


def _digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_READ_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()
