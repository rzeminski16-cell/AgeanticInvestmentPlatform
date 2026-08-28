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

__all__ = ["THEMES", "Shell", "shell_for"]

GUIDANCE_COOKIE: Final = "aer_guidance"
"""Where the guidance flag is remembered between pages.

ADR 0077 puts guidance mode on the server rather than in the client because it fails the
chrome test: a reload that lost it would be noticed. A cookie is the smallest durable place
that is per-operator without a migration on a table documented as holding one row, and the
flag is a preference rather than a record — nothing cites it and no figure depends on it.
"""

THEME_COOKIE: Final = "aer_theme"
"""Where the light/dark choice is remembered. Same reasoning as the guidance flag.

**Read on the server and stamped on ``<html>``, which is the point.** The usual way to do
this is a script in ``<head>`` that reads local storage and sets a class before first paint;
that is a whole scripting dependency bought to avoid a flash, on an application whose menu
deliberately works with scripting off. A cookie the renderer already has costs one attribute
and cannot flash at all.
"""

THEMES: Final = ("system", "light", "dark")
"""The three a person can choose. ``system`` is the absence of a choice, named.

Named rather than represented by a missing cookie, because "follow the machine" is a
position somebody can hold and go back to. A tri-state that spelt one of its states as
*unset* would make choosing it indistinguishable from never having chosen.
"""


@dataclass(frozen=True, slots=True)
class Shell:
    """What surrounds a page: where you are, and how to get somewhere else."""

    nav: tuple[NavSection, ...]
    active: str
    guidance: bool
    path: str

    # One of `THEMES`. Validated on the way in rather than on the way out: an unknown value
    # in a cookie is somebody's hand-edited jar, and the answer to one is the default.
    theme: str = "system"

    @property
    def guidance_attr(self) -> str:
        """The value of ``data-guidance`` on ``<body>``.

        A string rather than a boolean because it is going into an attribute, and a
        template that has to decide how to spell a boolean is a template making a
        presentation decision twice.
        """
        return "on" if self.guidance else "off"

    @property
    def location(self) -> str:
        """Where the operator is, in the words the navigation uses.

        Shown in the chrome at the widths where the index collapses. With scripting on at
        320px the index is closed, so its `aria-current` item is not on the screen and the
        page itself becomes the only thing saying which page it is — which means opening the
        menu to find out where you already are.

        Empty when nothing matches, which is a real state rather than an oversight: a page
        reachable but unlisted (`UNLISTED` in the registry) has no navigation item to name,
        and inventing one would put a label on the chrome that leads nowhere.
        """
        for section in self.nav:
            for item in section.items:
                if item.key == self.active:
                    return f"{section.label} · {item.label}"
        return ""

    @property
    def theme_attr(self) -> str:
        """The value of ``data-theme`` on ``<html>``.

        Empty for ``system``, and the emptiness is load-bearing: the dark palette is written
        as ``:root:not([data-theme="light"])`` inside the media query, so an absent attribute
        is what lets the operating system decide. An attribute reading ``system`` would be a
        third selector every colour rule had to know about.
        """
        return "" if self.theme == "system" else self.theme


def shell_for(path: str, *, guidance: bool = False, theme: str = "system") -> Shell:
    """The shell for a request at ``path``. No I/O, by design — see the module docstring."""
    return Shell(
        nav=NAV,
        active=active_key(NAV, path),
        guidance=guidance,
        path=path,
        theme=theme if theme in THEMES else "system",
    )
