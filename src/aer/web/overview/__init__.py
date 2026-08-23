"""The Overview screen: what is waiting, from whichever tool it is waiting in.

Split across four modules for one reason each. `nav.py` holds the section and imports
nothing heavy, because the shell composes the nav and the router imports the shell.
`attention.py` holds the registry every tool answers through. `research.py` and
`platform.py` are two tools' answers, kept apart so that a second tool is a file rather
than a branch. `pages.py` renders what they return and asks nothing itself.
"""

from __future__ import annotations

from aer.web.overview.attention import Attention, AttentionProvider, Severity, items_for
from aer.web.overview.nav import OVERVIEW

__all__ = ["OVERVIEW", "Attention", "AttentionProvider", "Severity", "items_for"]
