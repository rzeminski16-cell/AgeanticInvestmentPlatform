"""Navigation as data, so a second tool can contribute to it.

`_nav.html` was eight hand-written anchors, each repeating an identical class string, with
no active state and nothing that could notice when a link stopped resolving. That is fine
for one tool and impossible for several: a second tool's pages would either be unreachable
or would arrive by editing a template that belongs to the first.

So the nav is a tuple of frozen rows and the template is a loop. Composition is one import
per tool into `shell/registry.py`, deliberately mirroring `db/models/__init__.py` —
explicit registration plus a test that fails when you forget, rather than discovery that
quietly succeeds with less than you meant.

**It lives here rather than under `shell/` because of the cycle that arrangement creates.**
`shell/registry.py` imports each contributing tool's module, and those modules need these
types; importing them from `aer.web.shell.nav` runs `aer/web/shell/__init__.py` first,
which imports `shell/context.py`, which imports `shell/registry.py` — back to a package
half-way through initialising. It worked only while something imported `aer.web.shell`
first, and `import aer.web.tools.registry` in a fresh interpreter raised `ImportError`.
`aer/web/__init__.py` imports nothing, so a module beside it is reachable from anywhere in
this package. `tests/test_shell_nav.py` asserts no contributor reaches back.

**These rows describe navigation, not authorisation.** An item's presence in the sidebar is
not permission to reach the page behind it; the route's own dependencies decide that, as
they do today. A nav that could grant access would be a second, weaker place for a rule
this platform keeps in exactly one.
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["NavGroup", "NavItem", "NavSection", "active_key"]


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
    """One tool's contribution to the sidebar: its destinations, and nothing else.

    ``tool`` is the registry key of whatever contributed the section (ADR 0071), so a page
    can say which tool it is inside without a second lookup.

    **A section has no label, and losing it is the point** (ADR 0112). It had one, and it
    was the sidebar's heading, which made "one heading per tool" a rule nobody chose: at
    the second tool that reads as organisation and at the ninth it reads as a wall, with
    seven headings standing over a single link each and six of them repeating the word
    below. The heading is the group's now, and a section is the unit of *contribution*
    rather than the unit of *presentation*. Nothing about how a tool registers changed.
    """

    key: str
    tool: str
    items: tuple[NavItem, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class NavGroup:
    """A heading in the sidebar, over the sections of however many tools sit beneath it.

    The grouping is the **shell's** decision, not a tool's: a tool says what it offers, and
    where that sits in a menu is a judgement about the whole product that no single
    contributor can make. So a group is declared in `shell/registry.py` and a tool never
    names one — which is also what stops a ninth tool from adding a ninth heading simply by
    existing.

    ``label`` may be empty, and that renders the items with no heading at all. The home
    page needs it: a category of one, called the same thing as the link inside it, is the
    noise this whole arrangement exists to remove.
    """

    key: str
    label: str
    sections: tuple[NavSection, ...] = field(default_factory=tuple)

    @property
    def items(self) -> tuple[NavItem, ...]:
        """Every destination under this heading, in the order its sections declared them."""
        return tuple(item for section in self.sections for item in section.items)


def active_key(groups: tuple[NavGroup, ...], path: str) -> str:
    """Which item the current path is inside, or ``""``.

    The longest matching prefix wins, so ``/requests/new`` lights *Requests* rather than
    whichever item happened to be declared first. Ties cannot arise: two items with the
    same prefix would be two names for one destination.
    """
    best = ""
    best_length = -1
    for group in groups:
        for item in (*group.items, *(child for i in group.items for child in i.children)):
            if item.matches(path) and len(item.prefix) > best_length:
                best, best_length = item.key, len(item.prefix)
    return best
