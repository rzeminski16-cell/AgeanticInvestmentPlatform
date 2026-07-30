"""UK data sources: Companies House, and the issuer's own annual report.

The UK half of the universe the request form already accepts. Kept in its own package rather
than beside the SEC adapter because the two publishers share almost nothing — different
identifiers, different filing vocabulary, different licence terms — and a single ``sources``
namespace holding both would suggest a symmetry that does not exist.

**There is no FCA adapter and there is not going to be one.** The National Storage Mechanism
would be the obvious third source, but the FCA's terms prohibit automated access to its sites
without prior written consent and it offers no public read API. The refusal lives in
``aer.fetch.policy``, where it cannot be forgotten; the reasoning is ADR 0022.
"""

from __future__ import annotations
