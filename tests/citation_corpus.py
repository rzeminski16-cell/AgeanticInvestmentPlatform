"""A labelled citation corpus: forty claim/excerpt pairs whose answers are known in advance.

``docs/PLAN.md`` §2.10 calls for ``fx_msft_10k`` — hand-labelled pairs the verifier is scored
against. This is it.

**What ``genuine`` means, exactly.** *The stored excerpt is what the artefact says at the
recorded locator*, allowing for whitespace normalisation. Not "the words appear somewhere in
the document": a locator is part of a citation, and an excerpt that exists in a different
paragraph is a citation pointing at the wrong place. Several pairs below are real sentences
lifted to the wrong locator, and they are labelled ``genuine=False`` for that reason.

**The corpus contains fabrications, and it has to.** Scored against only-genuine pairs, a
verifier that returned ``True`` unconditionally would score 100% — and that is precisely the
failure the metric exists to detect. Roughly a third of these are wrong in one of six
different ways.

The document is a synthetic filing rather than a real 10-K: every sentence in it is one
somebody here wrote, so the labels are ours to be certain about, and no third party's
copyright is checked into the repository.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["FILING", "OTHER_FILING", "PAIRS", "Pair", "fabricated_pairs", "genuine_pairs"]

FILING = b"""<!DOCTYPE html>
<html><head><title>Annual Report on Form 10-K</title></head><body>
<h1>Contoso Corporation</h1>
<p>Total revenue was $198,270 million for fiscal year 2022.</p>
<p>Operating income was $83,383 million for fiscal year 2022.</p>
<p>Total revenue was $168,088 million for fiscal year 2021.</p>
<p>Operating income was $69,916 million for fiscal year 2021.</p>
<p>Gross margin was $135,620 million for fiscal year 2022.</p>
<p>Research and development expense was $24,512 million for fiscal year 2022.</p>
<p>Sales and marketing expense was $21,825 million for fiscal year 2022.</p>
<p>General and administrative expense was $5,900 million for fiscal year 2022.</p>
<p>Net income was $72,738 million for fiscal year 2022.</p>
<p>Diluted earnings per share were $9.65 for fiscal year 2022.</p>
<p>Cash and cash equivalents were $13,931 million at 30 June 2022.</p>
<p>Short-term investments were $90,826 million at 30 June 2022.</p>
<p>Total assets were $364,840 million at 30 June 2022.</p>
<p>Total liabilities were $198,298 million at 30 June 2022.</p>
<p>Stockholders equity was $166,542 million at 30 June 2022.</p>
<p>Long-term debt was $47,032 million at 30 June 2022.</p>
<p>Net cash from operations was $89,035 million for fiscal year 2022.</p>
<p>Capital expenditure was $23,886 million for fiscal year 2022.</p>
<p>Dividends declared were $18,135 million for fiscal year 2022.</p>
<p>Share repurchases were $32,696 million for fiscal year 2022.</p>
<p>The Productivity segment reported revenue of $63,364 million.</p>
<p>The Intelligent Cloud segment reported revenue of $75,251 million.</p>
<p>The Personal Computing segment reported revenue of $59,655 million.</p>
<p>Headcount was approximately 221,000 at 30 June 2022.</p>
<p>The effective tax rate was 13.1 per cent for fiscal year 2022.</p>
<p>Deferred revenue was $45,538 million at 30 June 2022.</p>
<p>Goodwill was $67,524 million at 30 June 2022.</p>
<p>Foreign currency movements reduced revenue growth by two percentage points.</p>
<p>The company operates in more than 190 countries and regions.</p>
<p>Unearned revenue is recognised rateably over the contract term.</p>
</body></html>"""

# A second, different filing. The excerpts lifted from it are real sentences that are simply
# not in the document being cited — the mistake a model makes when two filings are in context.
OTHER_FILING = b"""<!DOCTYPE html><html><body>
<p>Total revenue was $211,915 million for fiscal year 2023.</p>
<p>Net income was $72,361 million for fiscal year 2023.</p>
</body></html>"""


@dataclass(frozen=True, slots=True)
class Pair:
    """One claim/excerpt pair, and the answer.

    Args:
        anchor: The text to locate in the document. Its character range becomes the
            citation's locator, so every pair needs a distinct one — the extractions table
            is unique on it.
        stored: What is written into the extraction as the excerpt. ``None`` stores the
            anchor itself, which is the ordinary, correct case.
        genuine: Whether ``stored`` is what the artefact says at ``anchor``'s locator.
        why: How this pair is wrong, for the failure report. Empty for a genuine pair.
    """

    name: str
    anchor: str
    genuine: bool
    stored: str | None = None
    why: str = ""


def _real(text: str, name: str) -> Pair:
    return Pair(name=name, anchor=text, genuine=True)


# Every sentence in the filing, cited correctly. The bulk of the corpus, because the ordinary
# case is the ordinary case and a verifier that refused real excerpts would be useless in a
# different way from one that accepted fake ones.
_SENTENCES: tuple[str, ...] = (
    "Total revenue was $198,270 million for fiscal year 2022.",
    "Operating income was $83,383 million for fiscal year 2022.",
    "Total revenue was $168,088 million for fiscal year 2021.",
    "Operating income was $69,916 million for fiscal year 2021.",
    "Gross margin was $135,620 million for fiscal year 2022.",
    "Research and development expense was $24,512 million for fiscal year 2022.",
    "Sales and marketing expense was $21,825 million for fiscal year 2022.",
    "General and administrative expense was $5,900 million for fiscal year 2022.",
    "Net income was $72,738 million for fiscal year 2022.",
    "Diluted earnings per share were $9.65 for fiscal year 2022.",
    "Cash and cash equivalents were $13,931 million at 30 June 2022.",
    "Short-term investments were $90,826 million at 30 June 2022.",
    "Total assets were $364,840 million at 30 June 2022.",
    "Total liabilities were $198,298 million at 30 June 2022.",
    "Stockholders equity was $166,542 million at 30 June 2022.",
    "Long-term debt was $47,032 million at 30 June 2022.",
    "Net cash from operations was $89,035 million for fiscal year 2022.",
    "Capital expenditure was $23,886 million for fiscal year 2022.",
    "Dividends declared were $18,135 million for fiscal year 2022.",
    "Share repurchases were $32,696 million for fiscal year 2022.",
    "The Productivity segment reported revenue of $63,364 million.",
    "The Intelligent Cloud segment reported revenue of $75,251 million.",
    "The Personal Computing segment reported revenue of $59,655 million.",
    "Headcount was approximately 221,000 at 30 June 2022.",
    "The effective tax rate was 13.1 per cent for fiscal year 2022.",
    "Deferred revenue was $45,538 million at 30 June 2022.",
    "Goodwill was $67,524 million at 30 June 2022.",
)

_GENUINE: tuple[Pair, ...] = tuple(
    _real(sentence, f"genuine: {sentence[:40].rstrip()}") for sentence in _SENTENCES
)

# The same sentence with only its whitespace changed. Genuine: the verifier normalises before
# comparing, because an extractor that reflows a paragraph must not invalidate every citation
# already recorded against it.
_REFLOWED: tuple[Pair, ...] = (
    Pair(
        name="genuine: reflowed with a line break",
        anchor="Foreign currency movements reduced revenue growth by two percentage points.",
        genuine=True,
        stored=("Foreign currency movements reduced\n   revenue growth by two percentage points."),
    ),
    Pair(
        name="genuine: reflowed with doubled spaces",
        anchor="The company operates in more than 190 countries and regions.",
        genuine=True,
        stored="The  company  operates  in  more  than  190  countries  and  regions.",
    ),
)

# Six ways of being wrong, each a mistake somebody or something actually makes.
_FABRICATED: tuple[Pair, ...] = (
    Pair(
        name="fabricated: a plausible sentence that is not in the document",
        anchor="Unearned revenue is recognised rateably over the contract term.",
        genuine=False,
        stored="Total revenue was $250,000 million for fiscal year 2022.",
        why="invented outright",
    ),
    Pair(
        name="fabricated: one digit changed",
        anchor="Total revenue was $198,270 million",
        genuine=False,
        stored="Total revenue was $198,720 million",
        why="two digits transposed — the hardest kind to see and the easiest to make",
    ),
    Pair(
        name="fabricated: the right sentence at the wrong year's locator",
        anchor="Total revenue was $168,088 million for fiscal year 2021",
        genuine=False,
        stored="Total revenue was $198,270 million for fiscal year 2022.",
        why="a real sentence, cited at another year's paragraph",
    ),
    Pair(
        name="fabricated: a real sentence from a different filing",
        anchor="Net income was $72,738 million",
        genuine=False,
        stored="Total revenue was $211,915 million for fiscal year 2023.",
        why="lifted from the other document in context",
    ),
    Pair(
        name="fabricated: a segment's figure moved to another segment",
        anchor="The Intelligent Cloud segment reported revenue of $75,251",
        genuine=False,
        stored="The Intelligent Cloud segment reported revenue of $63,364 million.",
        why="the Productivity figure under the Cloud heading",
    ),
    Pair(
        name="fabricated: a unit inflated from millions to billions",
        anchor="Goodwill was $67,524 million",
        genuine=False,
        stored="Goodwill was $67,524 billion at 30 June 2022.",
        why="a thousandfold error that reads as a typo",
    ),
    Pair(
        name="fabricated: an excerpt truncated away from its locator",
        anchor="Net cash from operations was $89,035 million for fiscal",
        genuine=False,
        stored="Net cash",
        why="a fragment of the span, which is not what the span says",
    ),
    Pair(
        name="fabricated: a sentence padded with a conclusion the filing does not draw",
        anchor="The effective tax rate was 13.1 per cent",
        genuine=False,
        stored=(
            "The effective tax rate was 13.1 per cent for fiscal year 2022, and management "
            "expects it to fall further next year."
        ),
        why="a forecast attached to a reported figure",
    ),
    Pair(
        name="fabricated: a negation inserted",
        anchor="Dividends declared were $18,135 million",
        genuine=False,
        stored="Dividends declared were not $18,135 million",
        why="one word that reverses the meaning and barely moves the ratio",
    ),
    Pair(
        name="fabricated: an empty-looking excerpt",
        anchor="Headcount was approximately 221,000",
        genuine=False,
        stored="   ",
        why="nothing at all, which must not pass as a match for anything",
    ),
    Pair(
        name="fabricated: the same sentence in a different case",
        anchor="Share repurchases were $32,696 million",
        genuine=False,
        stored="SHARE REPURCHASES WERE $32,696 MILLION",
        why=(
            "an excerpt is quoted verbatim, so a case change is a change — and a comparison "
            "that folded case would accept 'NOT' for 'not'"
        ),
    ),
    Pair(
        name="fabricated: the document's title cited as a financial statement",
        anchor="Capital expenditure was $23,886 million",
        genuine=False,
        stored="Contoso Corporation",
        why="real text from the document, at a locator that says something else",
    ),
)

PAIRS: tuple[Pair, ...] = (*_GENUINE, *_REFLOWED, *_FABRICATED)


def genuine_pairs() -> tuple[Pair, ...]:
    return tuple(pair for pair in PAIRS if pair.genuine)


def fabricated_pairs() -> tuple[Pair, ...]:
    return tuple(pair for pair in PAIRS if not pair.genuine)
