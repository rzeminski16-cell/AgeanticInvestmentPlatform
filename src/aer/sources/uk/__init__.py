"""UK data sources: Companies House, and the FCA's National Storage Mechanism.

The UK half of the universe the request form already accepts. Kept in its own package rather
than beside the SEC adapter because the two publishers share almost nothing — different
identifiers, different filing vocabulary, different licence terms — and a single ``sources``
namespace holding both would suggest a symmetry that does not exist.
"""

from __future__ import annotations
