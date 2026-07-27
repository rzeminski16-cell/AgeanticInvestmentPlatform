"""Version and build identity.

Every job, calculation and rendered report records the code version that produced it, so
a report can be reproduced later against the exact code that generated its numbers. That
requirement is why this module exists at the foundation rather than being added later.

See ``docs/adr/0003-deterministic-code-owns-numbers-and-facts.md``.
"""

from __future__ import annotations

import shutil
import subprocess
from functools import lru_cache
from importlib import metadata
from typing import Final

__all__ = ["__version__", "build_identity", "git_sha", "version"]

_DISTRIBUTION_NAME: Final = "ageiantic-equity-research"
_FALLBACK_VERSION: Final = "0.0.0+unknown"
_GIT_TIMEOUT_SECONDS: Final = 5.0


@lru_cache(maxsize=1)
def version() -> str:
    """Return the installed distribution version.

    Falls back to a sentinel rather than raising: an un-installed source checkout should
    still be able to start, and the sentinel makes the situation obvious in logs.
    """
    try:
        return metadata.version(_DISTRIBUTION_NAME)
    except metadata.PackageNotFoundError:
        return _FALLBACK_VERSION


@lru_cache(maxsize=1)
def git_sha() -> str | None:
    """Return the current git commit SHA, or ``None`` when it cannot be determined.

    Returns ``None`` rather than raising when git is unavailable or the working directory
    is not a repository (for example, inside a built container that shipped without the
    ``.git`` directory). Callers must treat a missing SHA as a reproducibility warning,
    not an error.
    """
    git_executable = shutil.which("git")
    if git_executable is None:
        return None

    try:
        # The argument vector is a fixed literal and the executable path comes from
        # shutil.which, so no untrusted input reaches the subprocess.
        completed = subprocess.run(  # noqa: S603
            [git_executable, "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    if completed.returncode != 0:
        return None

    sha = completed.stdout.strip()
    return sha or None


def build_identity() -> str:
    """Return a single human-readable identity string, e.g. ``0.1.0 (a1b2c3d)``."""
    sha = git_sha()
    if sha is None:
        return f"{version()} (git sha unavailable)"
    return f"{version()} ({sha[:7]})"


__version__: Final = version()
