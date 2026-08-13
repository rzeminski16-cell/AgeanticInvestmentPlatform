"""Run the test suite with the files in a shuffled order.

`just test` runs the files in the same order every time, so a test coupled to another
file's committed rows or module-level state passes for ever — and fails the first time
anything moves. Two have been found that way: an `Agent` subclass leaked into
`__subclasses__()` by a test that meant to define an invalid one, and an artefact row a
fixture committed into a table its own cleanup did not name.

Lives here rather than inline in the justfile because a recipe body has to be indented and
a multi-line Python string cannot be — the first attempt broke the whole justfile, so every
recipe in it stopped working, including `just up`. A module is also runnable directly
(`uv run python -m tests.shuffled 20260811`) on any platform, which a bash shebang recipe is
not.

The seed is printed whichever way it was chosen, so a red run can always be repeated
exactly.
"""

from __future__ import annotations

import pathlib
import random
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent


def ordering(seed: int) -> list[str]:
    """Every unit-test file, shuffled deterministically for ``seed``.

    Sorted before shuffling so the same seed gives the same order regardless of what the
    filesystem happens to return — a reproducible ordering is the whole point.
    """
    paths = sorted(p.as_posix() for p in HERE.glob("test_*.py"))
    random.Random(seed).shuffle(paths)  # noqa: S311 -- a test order, not a secret
    return paths


def main(argv: list[str]) -> int:
    seed = int(argv[0]) if argv else random.randrange(1, 1_000_000)  # noqa: S311 -- as above
    print(f"test-shuffled seed: {seed}", flush=True)

    files = ordering(seed)
    if not files:
        print("no test files found", file=sys.stderr)
        return 1

    completed = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "pytest", *files, "-q", "--no-header"],
        cwd=HERE.parent,
        check=False,
    )
    if completed.returncode != 0:
        print(f"\nfailed under seed {seed}; repeat with: just test-shuffled {seed}", flush=True)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
