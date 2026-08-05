"""Removing credentials from URLs before anything records one.

Several data providers take their API key as a **query parameter** rather than a header —
FRED does, EODHD does. That is their choice and cannot be worked around, but it means the
credential is part of the URL, and a URL in this platform travels: to a log line, to the
``source_documents.url`` column, and to the sources appendix of a report. Three places a
credential must never be, all reached by doing nothing wrong.

**This module exists because that leak was real.** Task 25 gave the macro client its own
URL redactor and used it on the client's own log line and on the recorded URL — but
:class:`~aer.fetch.client.SafeFetcher` logs ``url`` and ``final_url`` itself, on every
completed fetch and on every retry, and those lines went out with the key in them.
:func:`aer.logging.redact_secrets` did not catch it: it masks by field *name* and by value
*shape*, and ``url`` is not a sensitive name while ``api_key=abc123`` matches no credential
shape. A per-adapter redactor cannot fix that, because the adapter is not the thing doing
the logging.

So the redaction lives here, in the fetch layer, and is applied where the URL is recorded.
The guarantee is then structural: an adapter cannot leak a query-string credential into a
log by forgetting to call anything.

**Redacting by parameter name, never by the key's value.** A key that has been rotated is
still hidden in an old log line, and a key that happens to look like ordinary text is still
hidden in a new one. Matching the value would fail at both ends.

**Every known credential parameter, not the current provider's.** The list below is applied
to every URL regardless of which adapter built it. A per-provider opt-in would be correct
until the day somebody adds an adapter and forgets, and the cost of that mistake is a
credential in a database row that outlives the subscription. Redacting anything that looks
like a credential parameter costs one needless substitution on a URL that has none.
"""

from __future__ import annotations

import re
from typing import Final

from aer.logging import CREDENTIAL_PARAMS

__all__ = ["CREDENTIAL_PARAMS", "REDACTION", "redact_credentials"]

# The names come from :mod:`aer.logging`, which is the authority on what a credential looks
# like and applies the same list to every log line — including ones written by libraries this
# codebase does not control. One list, so a name added for a new provider closes the hole in
# both places at once.
REDACTION: Final = "REDACTED"

_PATTERN: Final = re.compile(
    r"(?i)\b(" + "|".join(sorted(re.escape(name) for name in CREDENTIAL_PARAMS)) + r")=[^&#\s]*"
)


def redact_credentials(url: str) -> str:
    """The URL with every credential parameter's value replaced.

    The parameter *name* survives, so a reader can still see which credential was used and a
    reviewer can tell an authenticated request from an anonymous one. Only the value goes.

    >>> redact_credentials("https://eodhd.test/api/eod/MSFT.US?api_token=abc123&fmt=json")
    'https://eodhd.test/api/eod/MSFT.US?api_token=REDACTED&fmt=json'
    """
    return _PATTERN.sub(lambda match: f"{match.group(1)}={REDACTION}", url)
