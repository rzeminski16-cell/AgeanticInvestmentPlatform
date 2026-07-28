"""Server-rendered GUI: Jinja2 templates, static assets and the page routes.

The GUI is HTML rendered on the server, progressively enhanced with HTMX. It is a
separate package from ``aer.api`` on purpose: the JSON API is the contract, and the pages
are one consumer of it. Keeping them apart is what makes replacing this layer with a
single-page application later a swap rather than a rewrite. See
``docs/adr/0006-server-rendered-htmx-gui.md``.
"""

from __future__ import annotations
