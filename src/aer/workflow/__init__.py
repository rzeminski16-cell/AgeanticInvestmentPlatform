"""Workflows: a run as a sequence of recorded, resumable steps.

A research run takes twenty to sixty minutes and spends real money. Two properties follow
from that and shape everything here.

**A step that succeeded must never run twice.** Not for tidiness — re-running an acquisition
costs a request against a rate limit that blocks rather than throttles, and re-running a
model call costs money for an answer already held. Each step writes a ``job_steps`` row with
an idempotency key, and a completed step returns its stored output instead of executing.

**A run must survive the worker dying.** Restarting from the beginning would mean paying
twice for everything before the failure, and the failures worth surviving are exactly the
ones that happen late. The engine resumes from the first step that is not complete.

Steps are sequential. Concurrency would be faster and would make "what happened, in what
order, and what did it cost?" much harder to answer — which is the question this whole
layer exists to make answerable. Phase 3 can revisit it once there is something worth
parallelising.
"""

from __future__ import annotations
