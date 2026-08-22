"""Navigation as data, so a second tool can contribute to it.

`_nav.html` was eight hand-written anchors, each repeating an identical class string, with
no active state and nothing that could notice when a link stopped resolving. That is fine
for one tool and impossible for several: a second tool's pages would either be unreachable
or would arrive by editing a template that belongs to the first.

So the nav is a tuple of frozen rows and the template is a loop. Composition is one import
per tool into `registry.py`, deliberately mirroring `db/models/__init__.py` — explicit
registration plus a test that fails when you forget, rather than discovery that quietly
succeeds with less than you meant.

**These rows describe navigation, not authorisation.** An item's presence in the sidebar is
not permission to reach the page behind it; the route's own dependencies decide that, as
they do today. A nav that could grant access would be a second, weaker place for a rule
this platform keeps in exactly one.
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["NavItem", "NavSection", "active_key"]


@dataclass(frozen=True, slots=True)
class NavItem:
    """One destination in the sidebar.

    ``match_prefix`` decides what counts as "you are here". It defaults to ``href``, which
    is right for a leaf like ``/settings`` and wrong for a section whose children live
    beneath it — ``/requests`` should stay lit while the operator reads
    ``/requests/{id}/edit``. Stated per item rather than inferred, because inferring it
    means guessing whether a path segment is a child or a sibling.
    """

    key: str
    label: str
    href: str
    match_prefix: str = ""
    # Named here and counted elsewhere. The count is fetched off the critical render path
    # (a slow count in one tool must not make every page in the product slow), so what the
    # sidebar renders on first paint is a slot rather than a number.
    badge_key: str = ""
    children: tuple[NavItem, ...] = ()

    @property
    def prefix(self) -> str:
        return self.match_prefix or self.href

    def matches(self, path: str) -> bool:
        """Whether ``path`` is this item or something underneath it."""
        if self.prefix == "/":
            return path == "/"
        return path == self.prefix or path.startswith(f"{self.prefix}/")


@dataclass(frozen=True, slots=True)
class NavSection:
    """A group of destinations, usually one tool's.

    ``tool`` is the registry key of whatever contributed the section (ADR 0067), so a page
    can say which tool it is inside without a second lookup.
    """

    key: str
    label: str
    tool: str
    items: tuple[NavItem, ...] = field(default_factory=tuple)


def active_key(sections: tuple[NavSection, ...], path: str) -> str:
    """Which item the current path is inside, or ``""``.

    The longest matching prefix wins, so ``/requests/new`` lights *Requests* rather than
    whichever item happened to be declared first. Ties cannot arise: two items with the
    same prefix would be two names for one destination.
    """
    best = ""
    best_length = -1
    for section in sections:
        for item in (*section.items, *(child for i in section.items for child in i.children)):
            if item.matches(path) and len(item.prefix) > best_length:
                best, best_length = item.key, len(item.prefix)
    return best
