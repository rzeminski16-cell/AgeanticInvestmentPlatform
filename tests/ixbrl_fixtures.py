"""UK inline XBRL filings, built by hand so a truth set can state what is in them.

Constructed rather than recorded, for the same reason as the PDF fixtures: a real 200-page
annual report gives you one document whose correct extraction nobody can write down, so the
only available assertion is "it did not crash".

The three that matter:

* :data:`CLEAN_IFRS` — every tag from a shared taxonomy. Extracts without asking anyone.
* :data:`WITH_EXTENSION` — one filer-invented element among the standard ones. **The case the
  confirmation gate exists for**, and the plan's named risk for this phase: UK filers extend
  the taxonomy routinely, and an extension carrying the company's headline profit measure is
  not something a platform should map by guessing.
* :data:`REMOTE_TAXONOMY_ONLY` — names a taxonomy by URL and nothing else. Used to assert that
  loading it opens no socket.
"""

from __future__ import annotations

from datetime import date
from typing import Final

__all__ = [
    "CLEAN_IFRS",
    "CLEAN_IFRS_TRUTH",
    "EXTENSION_TAG",
    "NOT_TAGGED",
    "PERIOD_END",
    "PERIOD_START",
    "REMOTE_TAXONOMY_ONLY",
    "TAXONOMY_URL",
    "WITH_EXTENSION",
]

PERIOD_START: Final = date(2021, 7, 1)
PERIOD_END: Final = date(2022, 6, 30)

COMPANY_NUMBER: Final = "01234567"

# The URL the fixtures name. Never fetched — that is the point of the offline test.
TAXONOMY_URL: Final = (
    "https://xbrl.ifrs.org/taxonomy/2021-03-24/full_ifrs_entry_point_2021-03-24.xsd"
)

EXTENSION_TAG: Final = "acme:AdjustedEBITDAPreExceptionalItems"

_NAMESPACES: Final = """xmlns="http://www.w3.org/1999/xhtml"
      xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"
      xmlns:ixt="http://www.xbrl.org/inlineXBRL/transformation/2015-02-26"
      xmlns:xbrli="http://www.xbrl.org/2003/instance"
      xmlns:link="http://www.xbrl.org/2003/linkbase"
      xmlns:xlink="http://www.w3.org/1999/xlink"
      xmlns:iso4217="http://www.xbrl.org/2003/iso4217"
      xmlns:ifrs-full="http://xbrl.ifrs.org/taxonomy/2021-03-24/ifrs-full"
      xmlns:acme="http://www.acme-holdings.test/xbrl/2022\""""


def _header() -> str:
    return f"""<div style="display:none">
<ix:header>
  <ix:references>
    <link:schemaRef xlink:type="simple" xlink:href="{TAXONOMY_URL}"/>
  </ix:references>
  <ix:resources>
    <xbrli:context id="D2022">
      <xbrli:entity>
        <xbrli:identifier scheme="http://www.companieshouse.gov.uk/">{COMPANY_NUMBER}</xbrli:identifier>
      </xbrli:entity>
      <xbrli:period>
        <xbrli:startDate>{PERIOD_START.isoformat()}</xbrli:startDate>
        <xbrli:endDate>{PERIOD_END.isoformat()}</xbrli:endDate>
      </xbrli:period>
    </xbrli:context>
    <xbrli:context id="I2022">
      <xbrli:entity>
        <xbrli:identifier scheme="http://www.companieshouse.gov.uk/">{COMPANY_NUMBER}</xbrli:identifier>
      </xbrli:entity>
      <xbrli:period><xbrli:instant>{PERIOD_END.isoformat()}</xbrli:instant></xbrli:period>
    </xbrli:context>
    <xbrli:unit id="GBP"><xbrli:measure>iso4217:GBP</xbrli:measure></xbrli:unit>
  </ix:resources>
</ix:header>
</div>"""


def _fact(tag: str, value: str, context: str = "D2022", *, scale: str = "3") -> str:
    """One tagged number. ``scale`` is in thousands, as a UK report states its figures."""
    return (
        f'<ix:nonFraction name="{tag}" contextRef="{context}" unitRef="GBP" '
        f'decimals="-3" scale="{scale}" format="ixt:numdotdecimal">{value}</ix:nonFraction>'
    )


def _document(body: str) -> bytes:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<html {_NAMESPACES}>
<head><title>Acme Holdings plc — Annual Report 2022</title></head>
<body>
{_header()}
{body}
</body></html>""".encode()


# -- A filing whose every tag is from a shared taxonomy -----------------------------------------

CLEAN_IFRS: Final[bytes] = _document(f"""
<p>Revenue for the year was {_fact("ifrs-full:Revenue", "198270")} thousand.</p>
<p>Cost of sales was {_fact("ifrs-full:CostOfSales", "62650")} thousand.</p>
<p>Gross profit was {_fact("ifrs-full:GrossProfit", "135620")} thousand.</p>
<p>Operating profit was {_fact("ifrs-full:ProfitLossFromOperatingActivities", "83383")}.</p>
<p>Profit for the year was {_fact("ifrs-full:ProfitLoss", "72738")} thousand.</p>
<p>Total assets at the year end were {_fact("ifrs-full:Assets", "364840", "I2022")}.</p>
<p>Cash was {_fact("ifrs-full:CashAndCashEquivalents", "13931", "I2022")} thousand.</p>
""")

# What the fixture states, as canonical concepts and values in pounds. Written here so the test
# checks the extractor against the document rather than against itself. Values are the tagged
# figure multiplied by the `scale` of 3.
CLEAN_IFRS_TRUTH: Final[dict[str, int]] = {
    "revenue": 198_270_000,
    "cost_of_revenue": 62_650_000,
    "gross_profit": 135_620_000,
    "operating_income": 83_383_000,
    "net_income": 72_738_000,
    "assets": 364_840_000,
    "cash_and_equivalents": 13_931_000,
}


# -- The same filing, with one element the filer invented ----------------------------------------

# `acme:AdjustedEBITDAPreExceptionalItems` is not in any shared taxonomy. It is also, plausibly,
# the number the company leads its results announcement with — which is exactly why the platform
# must not guess what it means.
WITH_EXTENSION: Final[bytes] = _document(f"""
<p>Revenue for the year was {_fact("ifrs-full:Revenue", "198270")} thousand.</p>
<p>Operating profit was {_fact("ifrs-full:ProfitLossFromOperatingActivities", "83383")}.</p>
<p>Adjusted EBITDA was {_fact(EXTENSION_TAG, "91204")} thousand.</p>
""")


# -- Nothing but a taxonomy reference ------------------------------------------------------------

# Names a remote schema and tags one fact. Loading it must not reach the network.
REMOTE_TAXONOMY_ONLY: Final[bytes] = _document(
    f"<p>Revenue was {_fact('ifrs-full:Revenue', '198270')}.</p>"
)


# -- A UK annual report that is not tagged at all --------------------------------------------------

# Plain XHTML. The readable text extracts through the HTML extractor; there are no facts, and
# saying so is different from failing.
NOT_TAGGED: Final[bytes] = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>Acme Holdings plc — Annual Report 2022</title></head>
<body><p>Revenue for the year was 198,270 thousand pounds.</p></body></html>""".encode()
