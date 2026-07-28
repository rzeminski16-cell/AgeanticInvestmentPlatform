"""SEC EDGAR: the primary US fundamentals source.

Free, complete for US registrants, and — uniquely among free sources — genuinely
point-in-time. Every fact EDGAR returns carries the accession number and the date of the
filing that reported it, so "what did this company say its 2020 revenue was, as at March
2021?" is a question with a determinate answer rather than an approximation.

Four endpoints are used:

======================================  =============================================
``www.sec.gov/files/company_tickers*``  ticker and exchange to CIK
``data.sec.gov/submissions/CIK*.json``  every filing an entity has made, with dates
``data.sec.gov/api/xbrl/companyfacts``  every XBRL fact the entity ever tagged
``www.sec.gov/Archives/edgar/data/...`` the filing documents themselves
======================================  =============================================

**Access is conditional on identifying yourself.** The SEC requires a descriptive
``User-Agent`` naming a real contact, and enforces a fair-access limit of roughly ten
requests per second aggregated across ``sec.gov``. Exceeding it gets an IP blocked, not
throttled. :mod:`aer.fetch.policy` sets the rate at eight, and :class:`SecEdgarClient`
adds a further pause between sequential requests — see the note there for why both.

**The aggregate endpoints are not citable documents.** ``companyfacts`` is generated on
demand from whatever filings exist at that moment; it has no publication date of its own,
so it is recorded with none and quarantined under point-in-time rules. That is correct
rather than unfortunate: the citable thing is the *filing*, identified by its accession
number, and every fact parsed out of the aggregate carries that accession with it.
"""

from __future__ import annotations
