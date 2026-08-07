"""Writing into the vault, and nowhere else.

Every path a note wants to occupy comes through :meth:`VaultWriter.write`, which resolves
it and refuses anything outside the vault root or anywhere under the personal root. The
personal directory is not merely avoided by convention — no code path exists that could
reach it, because this module is the only writer and this is its only door
(section 2.8, rule 6; the same both-directions containment `Settings` validates at
startup).

Evergreen notes carry a sentinel. Everything above ``<!-- AER:END-GENERATED -->`` is the
platform's to regenerate; everything below it belongs to the person and survives a
regeneration byte for byte (rule 5). A generated note that is not evergreen is simply
rewritten whole.
"""

from __future__ import annotations

from pathlib import Path

import structlog

from aer.errors import AerError
from aer.obsidian.notes import SENTINEL

__all__ = ["VaultWriteError", "VaultWriter"]

_log = structlog.get_logger("aer.obsidian.vault")


class VaultWriteError(AerError):
    """A write tried to leave the vault, or no vault is configured."""

    code = "obsidian_vault_write_refused"


class VaultWriter:
    """The one door into the vault.

    Args:
        vault_root: Where the generated vault lives. Created if absent.
        personal_root: The person's own notes, if configured. Never written, and refused
            even if a path somehow resolves into it — the double check matters because
            the roots are configuration and configuration changes.
    """

    def __init__(self, vault_root: Path, *, personal_root: Path | None = None) -> None:
        self._root = vault_root.resolve()
        self._personal = personal_root.resolve() if personal_root is not None else None

    @property
    def root(self) -> Path:
        return self._root

    def write(self, relative: str, content: str) -> Path:
        """Write one note, wholesale. Returns the resolved path."""
        target = self._contained(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return target

    def regenerate(self, relative: str, generated: str) -> Path:
        """Rewrite the generated half of an evergreen note, preserving the person's half.

        The generated content is written above the sentinel; whatever currently sits
        below the sentinel is carried over byte for byte. A note that does not exist yet
        gets the sentinel and an empty personal half, which is the invitation to write
        below it.
        """
        target = self._contained(relative)
        personal_half = ""
        if target.exists():
            existing = target.read_text(encoding="utf-8")
            marker = existing.find(SENTINEL)
            if marker != -1:
                personal_half = existing[marker + len(SENTINEL) :]

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"{generated}{SENTINEL}{personal_half}", encoding="utf-8")
        return target

    def _contained(self, relative: str) -> Path:
        """The resolved target, or a refusal. The only way a path becomes writable."""
        candidate = (self._root / relative).resolve()

        if not candidate.is_relative_to(self._root):
            message = (
                f"Refusing to write {relative!r}: it resolves to {candidate}, outside "
                f"the vault root {self._root}."
            )
            raise VaultWriteError(message, context={"relative": relative})

        if self._personal is not None and (
            candidate.is_relative_to(self._personal) or self._personal.is_relative_to(candidate)
        ):
            message = (
                f"Refusing to write {relative!r}: it resolves into the personal notes "
                "directory, which the application never touches."
            )
            raise VaultWriteError(message, context={"relative": relative})

        # Defence in depth for the reserved subtree: 99-Personal is never written even
        # when no separate personal root is configured, because the tree reserves it.
        reserved = self._root / "99-Personal"
        if candidate.is_relative_to(reserved):
            message = (
                f"Refusing to write {relative!r}: 99-Personal is reserved for the "
                "person and the application never writes into it."
            )
            raise VaultWriteError(message, context={"relative": relative})

        return candidate
