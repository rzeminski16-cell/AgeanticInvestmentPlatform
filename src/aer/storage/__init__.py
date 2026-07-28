"""The evidence substrate: content-addressed, immutable artefact storage.

Every byte this platform fetches is stored here, addressed by the SHA-256 of its own
content. That single decision buys three properties the whole system rests on:

* **A claim can be checked.** A citation names an artefact hash and an excerpt; the
  verifier re-reads the artefact by hash and confirms the excerpt is really in it. Only
  code confirms a citation — see ``docs/adr/0003``.
* **Tampering is detectable.** The address *is* the digest, so a file whose content no
  longer hashes to its own name has been altered.
* **Deduplication is free.** The same filing fetched twice is one file and one row,
  without anyone having to think about it.

The store knows nothing about the database. It moves bytes and returns a digest; rows,
provenance and dedup decisions belong to ``aer.services.artefacts``. Keeping the boundary
sharp is what will make an S3 backend a drop-in later rather than a rewrite.
"""

from __future__ import annotations
