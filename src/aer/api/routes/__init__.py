"""JSON API routers.

Every router here is mounted by :func:`aer.api.app.create_app`. Server-rendered pages
live in ``aer.web`` instead, so that the typed API and the HTML that happens to consume
it never become the same code.
"""

from __future__ import annotations
