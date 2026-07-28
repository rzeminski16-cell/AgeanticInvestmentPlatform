"""Data-source adapters: one package per publisher.

An adapter's job is to turn a publisher's idea of the world into this platform's. It
resolves an entity, discovers what documents exist, and parses them into typed facts. It
does **not** decide what a number means, and it does not calculate anything.

Three rules apply to every adapter here.

**Parsing is pure; fetching is not.** Each adapter separates the two: a ``client`` module
that talks to :class:`~aer.fetch.client.SafeFetcher`, and parser modules that take bytes
and return typed objects. The parsers are where the subtle bugs live — a columnar JSON
index whose arrays disagree in length, a date format that changes between years — and
keeping them free of I/O means every one of those cases is a two-line test rather than a
recorded HTTP interaction.

**No adapter takes a URL from outside itself.** URLs are constructed here, from
identifiers the publisher issued. That is what makes the "no agent-callable tool accepts a
URL" property of :mod:`aer.fetch` hold all the way up the stack rather than only at the
socket.

**Every fetched byte is archived before it is parsed.** A parse that fails still leaves
the evidence of what it tried to parse, which is the difference between a bug you can fix
and one you can only guess at.
"""

from __future__ import annotations
