"""An investor-relations page, including the links that must not be followed.

**The rejected links are the point of this fixture.** An IR page is the first content in the
platform whose links become candidate URLs, so the interesting cases are not the annual report
— they are the tracker on a third-party domain, the ``mailto:``, the ``data:`` URI that has no
host to check at all, and the ``<base href>`` pointing somewhere else, which is the obvious way
to defeat a host check by making every relative link resolve off-domain.

Written as one page rather than several because that is how they arrive: a real IR page has the
document you want and forty things you do not, interleaved.
"""

from __future__ import annotations

from typing import Final

__all__ = ["IR_HOST", "IR_PAGE", "IR_PAGE_URL", "IR_WITH_HOSTILE_BASE"]

IR_HOST: Final = "investors.example-issuer.test"
IR_PAGE_URL: Final = f"https://{IR_HOST}/financials/annual-reports"


IR_PAGE: Final[bytes] = f"""<!DOCTYPE html>
<html><head><title>Investor Relations — Reports</title></head>
<body>
  <nav>
    <a href="/about">About us</a>
    <a href="/careers">Careers</a>
    <a href="https://{IR_HOST}/contact">Contact</a>
  </nav>

  <h1>Reports and presentations</h1>
  <ul>
    <!-- Wanted: named in the link text, relative URL. -->
    <li><a href="/media/2022/ar-final.pdf">Annual Report 2022</a></li>

    <!-- Wanted: nothing in the text, but the URL says what it is. -->
    <li><a href="/media/2022/interim-report-2022.pdf">Download</a></li>

    <!-- Wanted, and carries an unambiguous date in its own text. -->
    <li><a href="/media/results.pdf">Interim results — 28 July 2022</a></li>

    <!-- Wanted: an HTML transcript, matched on the words rather than the extension. -->
    <li><a href="/events/q4-transcript">Q4 earnings call transcript</a></li>

    <!-- Wanted: a bare PDF from an IR page is a document whatever it is called. -->
    <li><a href="/media/8912.pdf">2022</a></li>

    <!-- Refused: a different host. The control this module exists for. -->
    <li><a href="https://cdn.other-domain.test/reports/ar-2022.pdf">Annual Report (mirror)</a></li>

    <!-- Refused: a lookalike host. `endswith` on the bare domain would admit this. -->
    <li><a href="https://evil-{IR_HOST}/annual-report.pdf">Annual Report 2022</a></li>

    <!-- Refused: not a fetch. -->
    <li><a href="mailto:ir@example-issuer.test">Email investor relations</a></li>
    <li><a href="javascript:openReport()">Annual Report (interactive)</a></li>

    <!-- Refused: a data URI has no host to check, which is the appeal of it. -->
    <li><a href="data:application/pdf;base64,JVBERi0xLjcK">Annual Report</a></li>

    <!-- Refused: on-domain and not a document. -->
    <li><a href="/governance/board">Board of directors</a></li>

    <!-- The same document twice, differing only by fragment. One candidate. -->
    <li><a href="/media/2022/ar-final.pdf#page=42">Annual Report 2022, segment note</a></li>
  </ul>
</body></html>""".encode()


# The same page with a `<base href>` pointing at another domain. Honouring it would make every
# relative link above resolve off-domain, which is precisely why it is not honoured: the base
# for resolution is the URL the fetch actually used.
IR_WITH_HOSTILE_BASE: Final[bytes] = b"""<!DOCTYPE html>
<html><head><base href="https://attacker.test/collect/"><title>Reports</title></head>
<body>
  <a href="/media/2022/ar-final.pdf">Annual Report 2022</a>
  <a href="quarterly-report.pdf">Quarterly Report</a>
</body></html>"""
