"""One async runner in the test process, kept that way.

CI runs 536 and 543 (ROADMAP §3.19, items 52 and 61) failed 608 and 610 tests from one
cause: two pytest plugins each willing to run an async fixture — pytest-asyncio in auto
mode, which takes every coroutine test and fixture, and anyio's, engaged by the marker
forty modules carried. Each wraps ``pytest_fixture_setup``, and the later-registered
plugin's wrapper is the outer one: it takes the fixture and the other steps aside.
Registration follows the directory listing of ``site-packages``, which a fresh virtual
environment orders as it likes. When anyio came second it ran ``db_engine`` and
``db_session`` on a loop of its own while pytest-asyncio ran the test on another, and
asyncpg said so: *Task got Future attached to a different loop*. On the machine the suite
was written on the listing put anyio first, so its plugin never engaged and every run was
green.

So there is one runner. anyio's plugin is blocked in ``addopts``, the marker is gone, and
this module fails if either comes back.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# Spelled apart so this file does not match its own scan.
_MARKER = "mark." + "anyio"


def test_the_anyio_plugin_is_blocked(pytestconfig: pytest.Config) -> None:
    assert "no:anyio" in pytestconfig.getoption("plugins"), (
        "addopts in pyproject.toml must carry `-p no:anyio`: with anyio's plugin loaded, "
        "which of two runners takes an async fixture follows the venv's directory order"
    )
    assert not pytestconfig.pluginmanager.has_plugin("anyio")


def test_pytest_asyncio_runs_everything_async(pytestconfig: pytest.Config) -> None:
    assert pytestconfig.getini("asyncio_mode") == "auto"


def test_no_test_module_asks_for_the_other_runner() -> None:
    marked = sorted(
        str(path.relative_to(REPO_ROOT))
        for path in (REPO_ROOT / "tests").rglob("*.py")
        if _MARKER in path.read_text(encoding="utf-8")
    )
    assert marked == [], f"the anyio marker is back in {marked}; there is one runner"
