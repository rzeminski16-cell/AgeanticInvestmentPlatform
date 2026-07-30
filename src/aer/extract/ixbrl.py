"""Inline XBRL: the tagged numbers inside a UK annual report.

A UK filing is one XHTML document that is *both* the readable report and the machine-readable
data. :mod:`aer.extract.html` already produces the readable half — `sniff` classifies inline
XBRL as HTML precisely so that works. This module produces the other half: the facts.

**`arelle` runs offline, and that is a control rather than a setting.** An iXBRL document names
its taxonomy by URL, and arelle's default behaviour is to go and fetch it. That would be a
component other than :mod:`aer.fetch` making an outbound request, driven by a URL inside an
untrusted document, past every allowlist and SSRF check in the platform — the exact shape of
the thing this architecture is built to prevent. :func:`extract_ixbrl` sets ``workOffline``
before loading anything, and a test asserts that a document naming a remote taxonomy opens no
socket.

**The cost of offline is that concepts do not resolve**, and it is worth being precise about
what that does and does not lose. Without the schema arelle cannot tell you an element's data
type, its balance sign, or its place in a calculation tree. It still gives you everything a
fact *is*: the tag's qualified name, the value with the document's own ``scale`` already
applied, the decimals, the period, the entity identifier and the unit. That is enough to
produce a :class:`~aer.core.schemas.facts.RawFact`, and the missing half is not something this
platform was going to trust a taxonomy for anyway — units are checked by :mod:`aer.calc.units`
and signs by the concept vocabulary.

**Completeness is about tags, not about schemas.** Offline, *no* concept resolves, so a
"partial because arelle could not resolve the schema" verdict would fire on every document and
be a badge nobody reads. The verdict that matters is whether a fact's tag maps to a canonical
concept: `ifrs-full:Revenue` does, and `mycompany:AdjustedEBITDAPreExceptionals` does not,
because it is an extension the filer invented. UK filings extend the taxonomy routinely — the
plan names that as this phase's main risk — so an extraction carrying unmapped tags is
:attr:`~IxbrlExtraction.needs_confirmation`, and the facts behind those tags do not become
evidence until a person says which concept, if any, each one means.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import tempfile
import warnings
from collections.abc import Iterator
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Final

from aer.core.concepts import canonical_concept
from aer.extract.errors import ParseFailedError, UnextractableError

__all__ = [
    "EXTRACTOR",
    "VERSION",
    "IxbrlExtraction",
    "IxbrlFact",
    "extract_ixbrl",
]

EXTRACTOR: Final = "ixbrl"
VERSION: Final = "1"

# Namespace prefixes that identify a shared taxonomy, mapped to the short name the concept
# vocabulary keys on. Anything not here is a filer extension by definition.
_TAXONOMY_BY_NAMESPACE: Final[dict[str, str]] = {
    "http://xbrl.ifrs.org/taxonomy": "ifrs-full",
    "http://www.xbrl.org/2003/instance": "xbrli",
    "http://xbrl.frc.org.uk/cd": "uk-bus",
    "http://xbrl.frc.org.uk/fr": "uk-core",
    "http://xbrl.frc.org.uk/reports": "uk-core",
    "http://fasb.org/us-gaap": "us-gaap",
    "http://xbrl.sec.gov/dei": "dei",
}

# Facts a filing tags that are not financial statement lines: the cover-page metadata, the
# entity's own name, the accountants' details. Kept out of the fact set because they are not
# numbers and would otherwise dominate the unmapped-tag count that drives the gate.
_NON_NUMERIC_KINDS: Final[frozenset[str]] = frozenset({"nonNumeric", "tuple"})


@dataclass(frozen=True, slots=True)
class IxbrlFact:
    """One tagged number, as the document states it.

    Deliberately *not* a :class:`~aer.core.schemas.facts.RawFact`. That type also carries the
    form, the filing identifier and the filed date, none of which are inside the document —
    they come from the index that pointed at it. The adapter joins the two; this module
    reports only what the bytes say.
    """

    taxonomy: str
    tag: str
    value: Decimal
    unit: str
    period_end: dt.date
    period_start: dt.date | None = None
    decimals: int | None = None
    entity_scheme: str | None = None
    entity_identifier: str | None = None
    context_id: str | None = None

    @property
    def qname(self) -> str:
        return f"{self.taxonomy}:{self.tag}"

    @property
    def concept(self) -> str | None:
        """The canonical concept this tag means, or ``None`` for an extension."""
        return canonical_concept(self.taxonomy, self.tag)

    @property
    def is_mapped(self) -> bool:
        return self.concept is not None


@dataclass(frozen=True, slots=True)
class IxbrlExtraction:
    """The facts a filing tagged, and whether they can be used without asking someone."""

    facts: tuple[IxbrlFact, ...] = field(default_factory=tuple)

    # Tags that reached no canonical concept, deduplicated and sorted. The list a person is
    # shown at the confirmation gate.
    unmapped_tags: tuple[str, ...] = field(default_factory=tuple)

    # What arelle complained about while loading. Kept because a filing that fails XBRL
    # validation may still yield usable facts, and the reviewer deciding whether to trust
    # them should see the complaints rather than a boolean.
    load_errors: tuple[str, ...] = field(default_factory=tuple)

    @property
    def mapped_facts(self) -> tuple[IxbrlFact, ...]:
        """Facts whose tag reached a canonical concept. The ones usable without a decision."""
        return tuple(fact for fact in self.facts if fact.is_mapped)

    @property
    def needs_confirmation(self) -> bool:
        """Whether a person must look at this before its facts become evidence.

        True when the filing used tags outside the shared taxonomies. **This is the gate the
        plan calls for**, and the reason it is on unmapped *tags* rather than on a count or a
        ratio: one extension element carrying the company's headline profit measure matters,
        and forty carrying segment breakdowns nobody asked for do not. Only a person can tell
        which, and the platform's job is to make sure they are asked rather than to guess.
        """
        return bool(self.unmapped_tags)

    @property
    def is_empty(self) -> bool:
        return not self.facts


def extract_ixbrl(data: bytes) -> IxbrlExtraction:
    """Extract the tagged facts from an inline XBRL document.

    Runs ``arelle`` with its web cache switched off, over a temporary copy of the bytes —
    arelle loads from a path rather than from memory, and giving it the artefact store's own
    file would let a parser bug write into the evidence.

    Raises:
        UnextractableError: The document parsed and tagged nothing. A UK annual report that is
            not inline XBRL looks like this, and so does one whose tagging is in an attachment.
        ParseFailedError: arelle could not load the document at all.
    """
    # Imported here rather than at module scope. `arelle` pulls in numpy, openpyxl and a
    # dozen other packages and costs the best part of a second to import; this module is
    # reached only when a UK filing is being read, and the web process should not pay for it.
    from arelle import Cntlr  # noqa: PLC0415

    with tempfile.TemporaryDirectory(prefix="aer-ixbrl-") as directory:
        document = Path(directory) / "filing.xhtml"
        document.write_bytes(data)

        controller = Cntlr.Cntlr(logFileName=None, disable_persistent_config=True)
        # **Before any load.** See the module docstring: this is the control that stops an
        # untrusted document's taxonomy URL becoming an outbound request.
        controller.webCache.workOffline = True

        try:
            model = controller.modelManager.load(str(document))
        except Exception as exc:
            message = f"arelle could not load the document: {type(exc).__name__}: {exc}"
            raise ParseFailedError(message, context={"extractor": EXTRACTOR}) from exc

        if model is None:
            message = "arelle returned no model for the document."
            raise ParseFailedError(message, context={"extractor": EXTRACTOR})

        facts = tuple(_facts_from(model))
        errors = tuple(str(error) for error in getattr(model, "errors", ())[:_ERROR_LIMIT])

        # A close failure must not lose the facts that were already read out of the model.
        with contextlib.suppress(Exception):
            controller.modelManager.close()

    if not facts:
        message = (
            "The document contains no inline XBRL facts. A UK annual report that tags nothing, "
            "or one whose tagging lives in a separate attachment, looks exactly like this — "
            "the readable text is still extractable through the HTML extractor."
        )
        raise UnextractableError(message, context={"extractor": EXTRACTOR, "bytes": len(data)})

    unmapped = tuple(sorted({fact.qname for fact in facts if not fact.is_mapped}))
    return IxbrlExtraction(facts=facts, unmapped_tags=unmapped, load_errors=errors)


_ERROR_LIMIT: Final = 20


# -- Reading arelle's model --------------------------------------------------------------------


def _facts_from(model: Any) -> Iterator[IxbrlFact]:
    """Every numeric fact in the model that carries enough to be one.

    A fact missing a period, a unit or a parseable value is skipped rather than defaulted. A
    filing has thousands of facts and one unusable row is not a reason to abandon the rest —
    the same rule the SEC parser follows, and for the same reason.
    """
    # arelle's model elements warn on truth-testing, and this suite turns warnings into
    # errors. Every check below is written as `is not None`; the filter is a belt-and-braces
    # against arelle doing its own truth-test internally while we walk the model.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FutureWarning)

        for fact in getattr(model, "facts", ()):
            parsed = _one_fact(fact)
            if parsed is not None:
                yield parsed


def _one_fact(fact: Any) -> IxbrlFact | None:
    qname = getattr(fact, "qname", None)
    if qname is None or getattr(fact, "isNil", False):
        return None
    if str(getattr(fact, "localName", "")) in _NON_NUMERIC_KINDS:
        return None

    context = getattr(fact, "context", None)
    unit = getattr(fact, "unit", None)
    if context is None or unit is None:
        # A numeric fact needs both. One with neither is a `nonNumeric` tag — a company name,
        # a director's signature — which is not a financial statement line.
        return None

    value = _decimal(getattr(fact, "value", None))
    period = _period(context)
    measure = _unit(unit)
    if value is None or period is None or measure is None:
        return None

    start, end = period
    scheme, identifier = _entity(context)
    return IxbrlFact(
        taxonomy=_taxonomy(qname),
        tag=str(qname.localName),
        value=value,
        unit=measure,
        period_start=start,
        period_end=end,
        decimals=_decimals(fact),
        entity_scheme=scheme,
        entity_identifier=identifier,
        context_id=str(getattr(fact, "contextID", "") or "") or None,
    )


def _taxonomy(qname: Any) -> str:
    """The short taxonomy name for a fact's namespace.

    Matched on a namespace *prefix* because the taxonomies carry a version in their URI —
    `http://xbrl.ifrs.org/taxonomy/2021-03-24/ifrs-full` and the 2023 edition are the same
    taxonomy, and keying on the whole URI would make every year a different one.

    An unrecognised namespace returns the document's own prefix, which is what a filer
    extension looks like and is exactly the string a reviewer needs to see at the gate.
    """
    namespace = str(getattr(qname, "namespaceURI", "") or "")
    for known, short in _TAXONOMY_BY_NAMESPACE.items():
        if namespace.startswith(known):
            return short
    return str(getattr(qname, "prefix", "") or "") or namespace or "unknown"


def _decimal(value: Any) -> Decimal | None:
    """The fact's value, with the document's ``scale`` already applied by arelle.

    ``Decimal`` rather than float, like every other number in this platform. A value arelle
    could not transform comes back as its error marker rather than a number, and that parses
    to ``None`` here — which is the right outcome: a fact whose value could not be read is
    not a fact.
    """
    if value is None:
        return None
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, ValueError, ArithmeticError):
        return None


def _decimals(fact: Any) -> int | None:
    raw = getattr(fact, "decimals", None)
    if raw is None:
        return None
    try:
        return int(str(raw).strip())
    except ValueError:
        # `decimals="INF"` is legal and means "exact". Recorded as absent rather than as a
        # number, because no integer means the same thing.
        return None


def _period(context: Any) -> tuple[dt.date | None, dt.date] | None:
    """``(start, end)`` for a duration, ``(None, instant)`` for a moment.

    **arelle reports an exclusive end**: a period ending 30 June comes back as 1 July, because
    that is how XBRL defines a duration internally. One day is subtracted so the stored date is
    the one printed in the accounts. Getting this wrong moves every UK fiscal year end by a
    day, which is the kind of error that survives review because it looks almost right.
    """
    instant = getattr(context, "instantDatetime", None)
    if instant is not None:
        return None, _as_date(instant) - dt.timedelta(days=1)

    start = getattr(context, "startDatetime", None)
    end = getattr(context, "endDatetime", None)
    if start is None or end is None:
        return None
    return _as_date(start), _as_date(end) - dt.timedelta(days=1)


def _as_date(value: Any) -> dt.date:
    return value.date() if isinstance(value, dt.datetime) else value


def _unit(unit: Any) -> str | None:
    """The unit as a short string: ``GBP``, ``USD``, ``shares``, or a ratio of two.

    A measure's local name is what matters — `iso4217:GBP` is GBP — and the namespace is the
    ISO standard rather than information. A divided unit (earnings per share, say) is rendered
    as ``GBP/shares`` so that :mod:`aer.calc.units` can parse it back.
    """
    measures = getattr(unit, "measures", None)
    if not measures:
        return None

    numerators = [str(m.localName) for m in measures[0]] if measures[0] else []
    denominators = (
        [str(m.localName) for m in measures[1]] if len(measures) > 1 and measures[1] else []
    )
    if not numerators:
        return None

    top = "*".join(numerators)
    return f"{top}/{'*'.join(denominators)}" if denominators else top


def _entity(context: Any) -> tuple[str | None, str | None]:
    """The scheme and identifier the filing names its entity by.

    For a UK filing the scheme is Companies House and the identifier is the company number,
    which is how a fact extracted from a document is tied back to the company it is about
    without trusting a filename.
    """
    identifier = getattr(context, "entityIdentifier", None)
    if not identifier or len(identifier) != _ENTITY_PARTS:
        return None, None
    scheme, value = identifier
    return (str(scheme) or None), (str(value).strip() or None)


_ENTITY_PARTS: Final = 2
