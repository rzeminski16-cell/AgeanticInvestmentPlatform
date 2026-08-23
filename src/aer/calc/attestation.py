"""The grade of evidence under a figure, and what a shareable surface gets without it.

ADR 0069 admits a fourth record kind and then makes one promise about it: **a lineage
containing any attested node cannot reach a shareable rendering.** Not the node — the
lineage. One typed exchange rate three levels down taints the net asset value computed above
it, because a figure is only as evidenced as its weakest input.

This module is that promise, and it is a type rather than a flag.

**Why not a flag.** A boolean is an argument waiting to happen: the figure is right there in
the object, the flag says not to show it, and every future template, exporter, serialiser
and copy-paste is one ``if not internal_only`` away from showing it anyway. Somebody under
time pressure will reason that *this* surface is fine, and they will be wrong in a way
nothing catches. Nobody argues with a field that does not exist, because there is nothing to
argue about: the number is not in the object they were handed.

That is the third use of the move — ADR 0034's ``WithheldComps`` has no ``peers``, ADR
0029's ``ValuationMandate`` has no constructor for a bank — which makes it a house pattern
rather than a coincidence.

**A documented attestation propagates nothing.** It is as citable as a filing, and that is
the whole reason the grade is a distinction rather than a blanket "operator-supplied data is
second class". A custodian statement is a document with a hash, an extraction and a citation,
checked by the same verifier that checks a 20-F.

Pure and side-effect free. The walk is over a :class:`~aer.calc.engine.CalculationContext`
that already holds every record — no session, no query, and therefore no way for the
containment to be skipped by a caller who did not have one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from aer.calc.units import CalculationError, Quantity, SourceKind, SourceRef
from aer.core.enums import Grade

if TYPE_CHECKING:
    from collections.abc import Iterable

    from aer.calc.engine import CalculationContext, CalculationRecord

__all__ = [
    "Attested",
    "Graded",
    "grade_of",
    "weakest_grade",
]

# How deep the lineage walk goes before giving up, matching the resolver's own bound. A real
# chain is perhaps ten deep; a hundred means something is wrong, and an unbounded walk over a
# cycle is a hung process rather than an error message.
_MAX_DEPTH = 100

# How many typed inputs the disclosure names before it starts counting. Three is enough to
# recognise which entries are the problem and short enough that the sentence stays one.
_NAMED_IN_DISCLOSURE = 3


@dataclass(frozen=True, slots=True)
class Graded:
    """A figure whose evidence is good enough to show anywhere.

    Only ever constructed for a lineage with no attested node in it, so ``quantity`` being
    present *is* the guarantee. :func:`grade_of` is the only thing that should build one.
    """

    quantity: Quantity
    label: str = ""

    @property
    def grade(self) -> Grade:
        return Grade.DOCUMENTED


@dataclass(frozen=True, slots=True)
class Attested:
    """What a shareable surface gets instead of a figure.

    **There is no ``quantity`` here and there must never be one.** A renderer handed this
    cannot print the number because the number is not in it. Everything below is about the
    *shape* of the work — how many typed inputs, and what they were called — which discloses
    that an answer exists without disclosing an answer nothing stands behind.
    """

    label: str
    attested_inputs: tuple[str, ...]

    @property
    def grade(self) -> Grade:
        return Grade.ATTESTED

    def as_sentence(self) -> str:
        """The disclosure, written here so it cannot vary by template."""
        typed = len(self.attested_inputs)
        named = ", ".join(self.attested_inputs[:_NAMED_IN_DISCLOSURE]) or "an unnamed entry"
        beyond = typed - _NAMED_IN_DISCLOSURE
        more = f" and {beyond} other(s)" if beyond > 0 else ""
        subject = self.label or "This figure"
        return (
            f"{subject} rests on {typed} figure(s) the operator typed rather than "
            f"documented — {named}{more} — so it is withheld from this version. It is "
            "shown in full on the operator's own copy, where the grade is stated beside it."
        )


def weakest_grade(sources: Iterable[SourceRef]) -> Grade:
    """The grade of a set of direct inputs: attested if any one of them is.

    **Not the most common grade and not the newest — the weakest.** Averaging evidence is
    how a book of one typed holding and nineteen documented ones comes to read as
    documented, and the typed one is the one a reader needed to know about.

    A reference that carries no grade at all contributes nothing: a filing is documented by
    definition and an assumption is a judgement rather than evidence, and neither is what
    this function exists to find.
    """
    for source in sources:
        if source.grade is Grade.ATTESTED:
            return Grade.ATTESTED
    return Grade.DOCUMENTED


def grade_of(
    context: CalculationContext, record: CalculationRecord, *, label: str = ""
) -> Graded | Attested:
    """Walk a calculation's whole lineage and hand back what may be shown.

    Depth-first through the ledger the context already holds: every input that is another
    calculation is followed, every input that is an attestation is graded, and everything
    else is a fact or an assumption and contributes nothing.

    Returns a :class:`Graded` carrying the figure, or an :class:`Attested` carrying no
    figure at all. **A caller cannot get the number out of the second one**, which is the
    entire mechanism — see the module docstring on why a flag would not do.

    Raises:
        CalculationError: If an input names a calculation this context does not hold, or if
            the walk exceeds :data:`_MAX_DEPTH`. Both mean the ledger being graded is not
            the ledger the figure was computed in, and answering anyway would be answering
            about a different lineage.
    """
    typed: list[str] = []
    seen: set[str] = set()
    pending: list[tuple[CalculationRecord, int]] = [(record, 0)]

    while pending:
        current, depth = pending.pop()
        if depth > _MAX_DEPTH:
            message = (
                f"Grading {record.name!r} walked more than {_MAX_DEPTH} levels. A lineage "
                "that deep is a cycle or a corrupt ledger, and either way the grade this "
                "would return is about neither."
            )
            raise CalculationError(message, context={"calculation": record.name})
        if str(current.id) in seen:
            continue
        seen.add(str(current.id))

        for source in current.input_sources:
            if source.kind is SourceKind.ATTESTATION:
                if source.grade is Grade.ATTESTED:
                    typed.append(source.label or source.identifier)
                continue
            if source.kind is not SourceKind.CALCULATION:
                continue
            child = context.find(source.identifier)
            if child is None:
                message = (
                    f"{current.name!r} cites calculation {source.identifier} and this "
                    "ledger does not hold it. A grade computed over a lineage with a hole "
                    "in it would be a claim about the part that was readable."
                )
                raise CalculationError(
                    message,
                    context={"calculation": current.name, "missing": source.identifier},
                )
            pending.append((child, depth + 1))

    if typed:
        # Deduplicated and sorted so the disclosure reads the same however the walk
        # happened to order the tree. A sentence that changes wording between two runs over
        # identical data is a sentence a reader learns to distrust.
        return Attested(label=label or record.name, attested_inputs=tuple(sorted(set(typed))))
    return Graded(
        quantity=Quantity.of(record.output_value, record.output_unit, source=record.source_ref),
        label=label or record.name,
    )
