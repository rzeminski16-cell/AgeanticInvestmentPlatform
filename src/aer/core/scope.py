"""What a run may see, as one value.

Two doors decide whether a piece of evidence is visible: `visible_facts` and
`visible_sources`. Each of them used to take a `ResearchRequest` and read two or three fields
off it, which had two costs. It made the equity mandate a dependency of the evidence layer,
so nothing without a ticker could ask the question at all. And a signature taking a whole
request invites a caller to pass one it happens to be holding for some other reason — which
is how ADR 0061's bug arrived in the first place, as a request-shaped proxy for "about the
subject".

`EvidenceScope` is the three fields those doors actually read, and no ticker to be tempted
by. It cannot be half-supplied. (It carried the run's date and a mode flag until ADR 0113
retired the rule that read them.)

**The asymmetry is the substance of it, not an accident of which fields fitted.** ADR 0061
decided that a fact is scoped by company and a source document by company *and* run, and
both halves are live:

* `visible_facts` filters on the company. The run appears nowhere in it *on purpose* —
  facts deduplicate on an observation key that excludes the source document, so they hang
  off whichever run fetched them first, and re-adding the run would hide every fact that run
  wrote. Five research workers once spent sixty tool calls searching a table that was full
  and looked empty.
* `visible_sources` filters on the run as well, because "what did this run acquire?" is
  exactly the question a sources page asks, and a document some other run fetched is not
  part of the answer.

So the scope carries `work_order_id` even though `visible_facts` ignores it. A value without
it would carry the fact half and drop the source half, leaving `visible_sources` to reach
back to a mandate table for the run identity — behind a value object introduced to remove
exactly that reach.

**A set-valued subject is a change to this file and its three callers**, which is the point
of putting the fields in one place. `subject_id` is one id today because `visible_facts` is a
single-subject predicate; a portfolio is the feature ADR 0061 anticipated when it said the
next thing to touch two companies would meet the same failure with nothing in place to catch
it. This does not answer that question. It gives it somewhere to be answered.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

__all__ = ["EvidenceScope"]

COMPANY = "company"
"""The only subject kind the evidence doors understand today."""


@dataclass(frozen=True, slots=True)
class EvidenceScope:
    """The scope a run reads evidence under.

    Frozen, and with no defaults: a scope assembled field by field is a scope that can be
    assembled wrongly, and the field it would be tempting to omit is the subject, which is
    the one that decides what a run is shown.
    """

    work_order_id: uuid.UUID
    subject_kind: str
    subject_id: uuid.UUID | None

    @property
    def company_id(self) -> uuid.UUID | None:
        """The subject as a company id, or ``None`` when it is not one.

        The evidence doors are written against companies, and will stay that way until
        something registers a second kind that has facts. A subject of another kind
        resolving to ``None`` here is the same state as an unresolved company: no facts,
        because there is nothing to match — the emptiness is the guard working.
        """
        if self.subject_kind != COMPANY:
            return None
        return self.subject_id
