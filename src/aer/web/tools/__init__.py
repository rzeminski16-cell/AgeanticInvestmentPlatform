"""The tool registry: what this platform has, and what it is going to have.

`registry.py` holds the rows and the one navigation section a tool under construction
earns; `pages.py` turns every row that is not built yet into a page at the URL that tool
will keep. A tool ships by changing its `status` and giving it real pages.
"""

from __future__ import annotations

from aer.web.tools.registry import (
    INSTALLED_TOOLS,
    PORTFOLIO,
    Tool,
    ToolStatus,
    installed_tools,
    resolve_tool,
    tools_needing_a_page,
)

__all__ = [
    "INSTALLED_TOOLS",
    "PORTFOLIO",
    "Tool",
    "ToolStatus",
    "installed_tools",
    "resolve_tool",
    "tools_needing_a_page",
]
