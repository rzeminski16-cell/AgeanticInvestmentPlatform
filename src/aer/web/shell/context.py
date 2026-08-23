"""The shell every page renders inside, assembled once per render.

`templating.render()` is the only way a page reaches a template, and it already carries the
disclaimer as a global for a stated reason: a page that forgets it presents personal
research as regulated advice, and "remember to include it" is not a control. The nav is the
same kind of thing. A handler that forgot to pass it would render a page with no way out of
it, and the failure would look like a styling bug.

So the shell is injected in `render()` and never by a handler.

**It must be constructible with no database.** `web/routes.py`'s landing page is designed to
render while Postgres is down — it reports the drift it cannot check — and `StrictUndefined`
means the moment `base.html` names `shell.nav`, any path that does not supply it raises
rather than degrading. A shell that needed a query would turn the one page an operator opens
when the database is off into a 500. Nothing here touches a session; the badge counts that
do are fetched separately and off the critical path.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from aer.web.nav import NavSection, active_key
from aer.web.shell.registry import NAV

__all__ = ["Shell", "shell_for"]

GUIDANCE_COOKIE: Final = "aer_guidance"
"""Where the guidance flag is remembered between pages.

ADR 0077 puts guidance mode on the server rather than in the client because it fails the
chrome test: a reload that lost it would be noticed. A cookie is the smallest durable place
that is per-operator without a migration on a table documented as holding one row, and the
flag is a preference rather than a record — nothing cites it and no figure depends on it.
"""


@dataclass(frozen=True, slots=True)
class Shell:
    """What surrounds a page: where you are, and how to get somewhere else."""

    nav: tuple[NavSection, ...]
    active: str
    guidance: bool
    path: str

    @property
    def guidance_attr(self) -> str:
        """The value of ``data-guidance`` on ``<body>``.

        A string rather than a boolean because it is going into an attribute, and a
        template that has to decide how to spell a boolean is a template making a
        presentation decision twice.
        """
        return "on" if self.guidance else "off"


def shell_for(path: str, *, guidance: bool = False) -> Shell:
    """The shell for a request at ``path``. No I/O, by design — see the module docstring."""
    return Shell(nav=NAV, active=active_key(NAV, path), guidance=guidance, path=path)
