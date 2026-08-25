"""The shell: what surrounds every page, and how a tool contributes to it."""

from __future__ import annotations

# Re-exported from `aer.web.nav`, which is where the types live so that a tool
# contributing a section does not have to import this package and close a loop.
from aer.web.nav import NavItem, NavSection, active_key
from aer.web.shell.badges import Badge, BadgeProvider, cached_counts_for, registered_badges
from aer.web.shell.context import (
    GUIDANCE_COOKIE,
    THEME_COOKIE,
    THEMES,
    Shell,
    shell_for,
)
from aer.web.shell.registry import NAV, UNLISTED, flat_items

__all__ = [
    "GUIDANCE_COOKIE",
    "NAV",
    "THEMES",
    "THEME_COOKIE",
    "UNLISTED",
    "Badge",
    "BadgeProvider",
    "NavItem",
    "NavSection",
    "Shell",
    "active_key",
    "cached_counts_for",
    "flat_items",
    "registered_badges",
    "shell_for",
]
