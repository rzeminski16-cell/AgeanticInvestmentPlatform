"""Recording what a document tried, so a reviewer sees it at gate 2.

One operation, and the important thing about it is what it does *not* do: it never quarantines,
never refuses, and never changes what happens to the run. A finding is information for a person.

That is not a gap in the defence. The defence is elsewhere and does not depend on detection —
agents have no network tool, tool allowlists are class attributes checked in Python, and fetched
content reaches a prompt wrapped and labelled. If the scanner missed every payload in the corpus,
none of those would weaken. See :mod:`aer.extract.injection`.
"""

from __future__ import annotations

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from aer.core.schemas.injection import Finding
from aer.db.models import SourceDocument

__all__ = ["record_findings"]

_log = structlog.get_logger("aer.services.injection")


async def record_findings(
    session: AsyncSession,
    *,
    document: SourceDocument,
    findings: tuple[Finding, ...],
) -> SourceDocument:
    """Attach a scan's findings to the document it scanned.

    Replaces rather than appends. A re-extraction is a fresh opinion about the same bytes under
    possibly newer heuristics, and accumulating both would leave a reviewer comparing two lists
    with no way to tell which is current.

    Clearing is therefore possible and correct: a document that no longer trips anything ends
    up unflagged, which is the honest outcome after a false-positive heuristic is removed.
    """
    document.injection_findings = (
        [finding.model_dump(mode="json", exclude_none=True) for finding in findings]
        if findings
        else None
    )
    document.injection_flagged = bool(findings)
    await session.flush()

    if findings:
        _log.warning(
            "source.injection_flagged",
            source_document_id=str(document.id),
            url=document.url,
            signals=sorted({finding.signal.value for finding in findings}),
            findings=len(findings),
        )
    return document
