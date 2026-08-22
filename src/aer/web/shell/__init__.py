"""The shell: what surrounds every page, and how a tool contributes to it."""

from __future__ import annotations

from aer.web.shell.context import GUIDANCE_COOKIE, Shell, shell_for
from aer.web.shell.nav import NavItem, NavSection, active_key
from aer.web.shell.registry import NAV, UNLISTED, flat_items

__all__ = [
    "GUIDANCE_COOKIE",
    "NAV",
    "UNLISTED",
    "NavItem",
    "NavSection",
    "Shell",
    "active_key",
    "flat_items",
    "shell_for",
]
