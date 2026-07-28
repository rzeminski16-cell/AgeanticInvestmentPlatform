"""Business operations, sitting between the HTTP layer and the database.

This is where a use case lives: "create a research request" means validate it against the
current time and configuration, check it is a security this platform can actually handle,
persist it, and record that it happened. A handler that did those four things itself
would be a handler the CLI and the workers could not reuse — and, more to the point, one
whose rules the *other* entry point would end up reimplementing slightly differently.

The rule for this layer: it may read the clock and the configuration, but it still does
no arithmetic on financial figures. That belongs in ``aer.calc``, where it is unit-tested
in isolation.
"""

from __future__ import annotations
